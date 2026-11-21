/* agent-life.js — the agents' standing figures are alive
   (docs/AGENT-LIFE.md, 2026-11-15; idle behaviours rebuilt the same day).
   Client dashboard only.

   1. IDLE BEHAVIOURS. Agents that stand waiting (minimised chat windows) do
      things a waiting person does — and each one in character: ג'וני checks
      his watch and keeps an eye on the team, אלינור looks at her nails and
      flips her hair, יואב rocks on his heels and thinks, אורי bounces and
      gets ideas, מאיה checks her phone, ליאור moves to a tune, ניסים
      stretches and shrugs. Two that stand side by side sometimes glance at
      each other, turn to talk, lean in, or laugh.
      HOW: the figure is one flat picture, so this script lays three masked
      copies of it (legs / upper body / head — same file, already cached) over
      it once, and a behaviour is ONE class on the figure that animates those
      layers (agent-life.css). A small bubble over the head shows what the
      agent is busy with where the picture cannot (arms do not move).
      Random figure, random behaviour (never the same twice in a row), random
      pauses — no loop.
      NEVER while a conversation is active (any open chat window, a reply
      being awaited, typing in the last few seconds) and never for an agent
      that is working (a running task or a pending reply in its own window).

   1b. POSE FRAMES (2026-11-22, a test on אורי only — docs/AGENT-LIFE.md
      §Pose frames). A character in POSES has real drawn poses (arms folded,
      a watch, thinking, glances, a wave): its idle behaviour is a short
      SEQUENCE of those frames, cross-faded, always ending on today's standing
      picture (the resting pose). Replaces the layered rig for that character
      only; off (admin setting uri_pose_frames_enabled, or ?poses=off) or a
      frame that fails to load = today's layered behaviours. The wave is only
      for arrival (the figure appears), at most once per GREET_GAP_MS — never
      a random idle. Same rules as every behaviour: the same scheduler,
      quiet(), working(), the switch, reduced motion, a hidden tab.

   2. ONE CELEBRATION per finished task, anywhere on the dashboard — confetti
      and a balloon. Once per task, merged when several finish together,
      queued (never stacked) when one finishes during another's burst.

   Rules that hold everywhere:
   - prefers-reduced-motion: nothing moves, nothing is built, nothing is drawn.
   - A hidden tab: no timer, no frame.
   - The client can turn it off (uallakLife.setEnabled(false), kept in
     localStorage) — the dashboard's "אזור אישי" has the switch.
   - No library, no network, no layout work: classes, transforms, one canvas.
   - Never MOVES a DOM node (the CLAUDE.md re-parenting trap). It adds: the
     rig inside a minimised figure (new nodes, nothing focusable) and the
     celebration canvas on <body>, which is removed when the burst ends.
*/
(function () {
  'use strict';
  if (window.uallakLife) return;                       // one instance even if loaded twice

  var KEY = 'uallak_life';                             // 'off' = the client switched it off
  var DONE_KEY = 'uallak_life_done';                   // task keys already celebrated (this browser)
  var FIGURE = '.chat-window.minimized .cw-figure-body';
  var OPEN_CHAT = '.chat-window:not(.minimized)';
  var PENDING = '.chat-window .typing';                // a reply is being awaited
  var WORKING = '.progress, .typing';                  // inside ONE window: its agent is working
  var MIN_TICK = 1300, MAX_TICK = 3400;                // ms between two looks at who is free
  var MIN_REST = 1800, MAX_REST = 5200;                // a figure's own pause after a behaviour
  var TYPING_QUIET_MS = 6000;
  var PAIR_CHANCE = 0.24, PAIR_REACH = 280;            // two side by side, px between their centres

  // Every behaviour and how long it plays (ms) — the same numbers as agent-life.css
  var MOVES = { nod: 1400, shake: 1500, tilt: 2600, look: 2600, lookaround: 4200, lookup: 3000, lookdown: 3400,
                shift: 3200, sway: 3600, stretch: 3400, shrug: 1600, bounce: 1500, rock: 4000, step: 2400,
                hairflip: 2000, groove: 3200, sigh: 3000, laugh: 1600, perk: 1500, turn: 3400, lean: 2800,
                hop: 750 };
  // Readable on the flat picture alone (a browser that cannot mask gets only these)
  var WHOLE = { shift: 1, bounce: 1, rock: 1, step: 1, lean: 1, hop: 1 };

  // Who does what: [behaviour, weight, bubble(s)]. The character IS this table.
  var CAST = {
    // the manager: composed, watches the time and the team
    johnny: [['lookaround', 16], ['lookdown', 16, '⌚'], ['shift', 14], ['nod', 12], ['rock', 10], ['stretch', 8],
             ['tilt', 6], ['perk', 6, '💡'], ['sigh', 4]],
    // easygoing: stretches, shrugs, checks his phone
    nissim: [['stretch', 16], ['lookdown', 14, '📱'], ['shrug', 12], ['sway', 10], ['look', 10], ['bounce', 10],
             ['laugh', 8], ['sigh', 8], ['step', 8]],
    // poised: nails, hair, a cool side glance
    elinor: [['hairflip', 16, '✨'], ['lookdown', 16, '💅'], ['shift', 16], ['tilt', 14], ['look', 12], ['lookup', 6],
             ['nod', 6], ['sigh', 6]],
    // the analyst: hands in pockets, rocking, thinking
    yoav: [['rock', 18], ['lookup', 16, '💭'], ['tilt', 12], ['nod', 12], ['lookdown', 10, '📊'], ['perk', 8, '👓'],
           ['sigh', 8], ['look', 8]],
    // the builder: cannot stand still, full of ideas
    uri: [['bounce', 18], ['lookaround', 14], ['perk', 14, '💡'], ['laugh', 10], ['nod', 10], ['step', 10],
          ['shrug', 6], ['sway', 6], ['lookdown', 6, '💻']],
    // warm and social: her phone, her hair, a small sway
    maya: [['lookdown', 16, '📱 💬 💅'], ['hairflip', 14], ['tilt', 14], ['sway', 12], ['look', 10], ['laugh', 8],
           ['shift', 8], ['perk', 6, '❤️']],
    // the artist: a tune in his head
    lior: [['groove', 20, '🎵 🎧'], ['sway', 14], ['lookup', 12, '✨'], ['bounce', 10], ['shake', 8],
           ['lookdown', 8, '🎬'], ['tilt', 8], ['shrug', 6], ['perk', 6, '🎨']],
    any: [['shift', 20], ['look', 18], ['nod', 14], ['stretch', 12], ['tilt', 12], ['rock', 10], ['sigh', 8]]
  };
  // Where each picture bends: [neck %, hips %, the body's centre line %] of the picture
  var RIG = { johnny: [22, 50, 54], nissim: [21, 50, 47.5], elinor: [23.5, 48, 50], yoav: [25, 51, 52.5],
              uri: [25.5, 54, 50], maya: [22.5, 44, 51], lior: [23, 52, 50] };
  // Two side by side. a = the one who starts, b = the neighbour, then one closing beat.
  var SCENES = [
    { a: ['look'], b: ['look'], gap: 600, then: ['b', 'nod', 2300] },                      // a glance, a nod back
    { a: ['turn', '💬'], b: ['turn'], gap: 700, then: ['a', 'laugh', 2700, '😄'] },         // a word, a laugh
    { a: ['lean'], b: ['tilt'], gap: 800, then: ['b', 'shrug', 2500] },                    // leaning in, "no idea"
    { a: ['perk', '👋'], b: ['look'], gap: 500, then: ['b', 'nod', 1900] },                 // "hey" — a nod
    { a: ['turn', '❓'], b: ['turn'], gap: 900, then: ['b', 'shake', 2600] }                // a question, "no"
  ];

  // Pose frames: real drawn poses on the SAME canvas as the standing picture
  // (scripts/normalize_pose_frames.ps1 + pack_pose_frames.js). frames = the
  // small nudge each one arrives with [x, y in --al-u, degrees]; idle =
  // [name, weight, steps], a step = [frame, hold ms], '<dir>' = left | right
  // (screen side); greet = the arrival. The sequence always ends on 'rest'.
  var POSES = {
    uri: { dir: '/assets/agents/uri/uri-',
           frames: { 'arms-folded': [0, 1, 0], 'watch': [0, 0, 0.8], 'thinking': [0, -1, -0.8],
                     'glance-left-neutral': [-1.5, 0, -0.8], 'glance-left-smile': [-0.5, 0, -0.3],
                     'glance-right-neutral': [1.5, 0, 0.8], 'glance-right-smile': [0.5, 0, 0.3],
                     'wave': [0, -1.5, 0] },
           idle: [['folded-glance', 18, [['arms-folded', 2000], ['glance-<dir>-neutral', 1300], ['glance-<dir>-smile', 1500]]],
                  ['glance', 18, [['glance-<dir>-neutral', 1500], ['glance-<dir>-smile', 1700]]],
                  ['think', 16, [['thinking', 3000]]],
                  ['watch', 14, [['watch', 2200]]],
                  ['folded', 12, [['arms-folded', 3200]]],
                  ['look', 10, [['glance-<dir>-neutral', 1800]]]],
           greet: [['wave', 1900]] }
  };
  var FADE_MS = 280;                                   // one cross-fade (agent-life.css .al-pose)
  var REST_NUDGE = [0, 0.6, 0];                        // the resting pose settles back in
  var GREET_GAP_MS = 10 * 60 * 1000;                   // a wave at most this often per figure

  var timer = null, lastFigure = null, lastTyped = 0, burstRunning = false, later = [];
  var busy = new WeakMap(), lastMove = new WeakMap(), plays = new WeakMap();
  var shown = new WeakMap(), greetDue = new WeakMap(), lastGreet = new WeakMap(), poseBroken = {};

  function store(kind) { try { return window[kind]; } catch (e) { return null; } }
  function now() { return Date.now(); }
  function between(min, max) { return min + Math.random() * (max - min); }
  function reduced() {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }
  function enabled() {
    var s = store('localStorage');
    try { return !(s && s.getItem(KEY) === 'off'); } catch (e) { return true; }
  }
  function allowed() { return enabled() && !reduced() && !document.hidden; }
  function canMask() {
    try {
      return !!(window.CSS && window.CSS.supports && (window.CSS.supports('mask-image', 'linear-gradient(#000, #000)') ||
        window.CSS.supports('-webkit-mask-image', 'linear-gradient(#000, #000)')));
    } catch (e) { return false; }
  }
  function wait(fn, ms) { var id = setTimeout(fn, ms); later.push(id); if (later.length > 40) later.shift(); return id; }

  // ─── The rig: three masked copies of the figure's own picture ─────────────
  function picture(el) {
    var holder = el && el.querySelector ? el.querySelector('.agent-figure') : null;
    var img = holder && holder.querySelector ? holder.querySelector('img') : null;
    if (!holder || !img || (holder.classList && holder.classList.contains('failed'))) return null;
    return { holder: holder, img: img, src: img.currentSrc || img.src || '' };
  }
  // 'maya' from /assets/agents/maya-figure.webp — the page keeps no second list of who is who
  function who(el) {
    var pic = picture(el), m = pic && /\/([a-z]+)-figure\.[a-z]+/i.exec(pic.src);
    return m ? m[1].toLowerCase() : '';
  }
  function layer(cls, src) {
    var span = document.createElement('span'), img = document.createElement('img');
    span.className = cls;
    img.alt = ''; img.draggable = false; img.decoding = 'sync'; img.src = src;
    span.appendChild(img);
    return span;
  }
  // The ONE place a node is placed in the page: a NEW node (the rig or the
  // pose stage) right before the figure's picture. Nothing existing moves.
  function addBefore(node, pic) { pic.holder.parentNode.insertBefore(node, pic.holder); }
  function drop(node) { if (node && node.parentNode) node.parentNode.removeChild(node); }
  function rigOf(el) {
    if (!el || !el.querySelector) return null;
    var rig = el.querySelector('.al-rig');
    if (rig) return rig;
    var pic = picture(el), spec = RIG[who(el)];
    if (posesOn(who(el))) return null;                 // drawn poses instead of the cut picture
    if (!pic || !spec || !pic.img.complete || !pic.img.naturalWidth || !canMask()) return null;
    rig = document.createElement('span');
    rig.className = 'al-rig';
    rig.setAttribute('aria-hidden', 'true');
    rig.style.setProperty('--al-neck', spec[0] + '%');
    rig.style.setProperty('--al-hip', spec[1] + '%');
    rig.style.setProperty('--al-px', spec[2] + '%');
    var upper = document.createElement('span'), emote = document.createElement('span');
    upper.className = 'al-upper';
    upper.appendChild(layer('al-torso', pic.src));
    upper.appendChild(layer('al-head', pic.src));
    emote.className = 'al-emote';
    rig.appendChild(layer('al-legs', pic.src));
    rig.appendChild(upper);
    rig.appendChild(emote);
    // A NEW node placed before the picture (CSS hides the picture while a rig
    // precedes it). When the page redraws the figure (another manager chosen)
    // both go together and the rig is rebuilt on the next behaviour.
    addBefore(rig, pic);
    return rig;
  }

  // ─── Pose frames ──────────────────────────────────────────────────────────
  // On unless: the owner's setting says false (the page puts it in
  // window.uallakPoseFrames from /api/dashboard), ?poses=off for a side-by-side
  // look, or a frame of the set failed to load on this page.
  function urlPoses() {
    try { var m = /[?&]poses=(on|off)(?:&|$)/.exec(window.location.search || ''); return m ? m[1] : ''; } catch (e) { return ''; }
  }
  function posesOn(name) {
    if (!POSES[name] || poseBroken[name]) return false;
    var forced = urlPoses();
    if (forced) return forced === 'on';
    var cfg = window.uallakPoseFrames;
    return !(cfg && cfg[name] === false);
  }
  function nudge(n) {
    return 'translate(calc(var(--al-u, 1px) * ' + n[0] + '), calc(var(--al-u, 1px) * ' + n[1] + ')) rotate(' + n[2] + 'deg)';
  }
  function poseImg(stage, owner, name, src, n, on) {
    var img = document.createElement('img');
    img.className = 'al-pose al-pose-' + name + (on ? ' al-on al-still' : '');
    img.alt = ''; img.draggable = false; img.decoding = 'async';
    // where it comes in from: set once, so its next entrance starts there
    img.style.setProperty('--al-from', nudge(n));
    img.onerror = function () { poseBroken[owner] = true; };
    img.src = src;
    if (!on && img.decode) { try { img.decode().catch(function () {}); } catch (e) { /* decoded on first show */ } }
    stage._f[name] = img;
    stage.appendChild(img);
  }
  // Built (= the frames start downloading) the first time the figure stands
  // visible while motion is allowed — never at page load, never under reduced
  // motion or with the switch off. The resting pose is today's picture itself
  // (already cached), so at rest the figure looks exactly as it does today.
  function stageOf(el, build) {
    var stage = el && el.querySelector ? el.querySelector('.al-poses') : null;
    if (stage || !build) return stage;
    var name = who(el), set = POSES[name], pic = picture(el);
    if (!set || !pic || !pic.img.complete || !pic.img.naturalWidth) return null;
    drop(el.querySelector('.al-rig'));                 // switched on after the rig was built
    stage = document.createElement('span');
    stage.className = 'al-poses';
    stage.setAttribute('aria-hidden', 'true');
    stage._f = {};
    poseImg(stage, name, 'rest', pic.src, REST_NUDGE, true);
    for (var f in set.frames) poseImg(stage, name, f, set.dir + f + '.png', set.frames[f], false);
    stage._cur = stage._f.rest;
    addBefore(stage, pic);
    return stage;
  }
  function loaded(img) { return !!(img && img.complete && img.naturalWidth); }
  // One cross-fade: the new frame fades in (and settles from its small nudge)
  // over the old one, which stays solid a moment and then fades — the body
  // never turns see-through half-way.
  function showPose(stage, name) {
    var next = stage._f[name], cur = stage._cur;
    if (!next || next === cur) return;
    next.classList.remove('al-off');
    next.classList.add('al-on');
    if (cur) {
      cur.classList.remove('al-on');
      cur.classList.remove('al-still');
      cur.classList.add('al-off');
      wait(function () { if (stage._cur !== cur) cur.classList.remove('al-off'); }, FADE_MS * 2);
    }
    stage._cur = next;
  }
  // Back to the resting pose: cross-faded, or at once (a hidden tab, stop)
  function poseRest(el, atOnce) {
    var stage = stageOf(el, false);
    if (!stage) return;
    if (!atOnce) { showPose(stage, 'rest'); return; }
    for (var f in stage._f) {
      stage._f[f].classList.remove('al-on');
      stage._f[f].classList.remove('al-off');
    }
    stage._f.rest.classList.add('al-still');
    stage._f.rest.classList.add('al-on');
    stage._cur = stage._f.rest;
  }
  // A sequence of frames, then the resting pose. 0 = nothing played (the
  // frames are still loading: the next free turn tries again).
  function posePlay(el, steps, label) {
    var stage = stageOf(el, true);
    if (!stage) return 0;
    for (var i = 0; i < steps.length; i++) if (!loaded(stage._f[steps[i][0]])) return 0;
    var turn = (plays.get(el) || 0) + 1, at = 0;
    plays.set(el, turn);
    steps.forEach(function (step) {
      wait(function () {
        if (plays.get(el) !== turn) return;
        // the client opened a chat, or the agent started working: back to rest, the rest is cancelled
        if (!quiet() || working(el)) { plays.set(el, turn + 0.5); poseRest(el); return; }
        showPose(stage, step[0]);
      }, at);
      at += FADE_MS + Math.round(step[1] * between(0.8, 1.25));
    });
    wait(function () { if (plays.get(el) === turn) poseRest(el); }, at);
    busy.set(el, now() + at + FADE_MS + between(MIN_REST, MAX_REST));
    lastMove.set(el, label);
    return at;
  }
  function poseSolo(el, name) {
    var set = POSES[name], last = lastMove.get(el);
    var options = set.idle.filter(function (s) { return s[0] !== last; });
    var choice = pick(options), side = Math.random() < 0.5 ? 'left' : 'right';
    var steps = choice[2].map(function (s) { return [s[0].replace('<dir>', side), s[1]]; });
    return posePlay(el, steps, choice[0]);
  }
  // Arrival: the figure stands visible now and did not on the previous look
  // (the page opened, or the client minimised the window) — a wave is due,
  // at most once per GREET_GAP_MS.
  function noteArrivals(figures) {
    var t = now(), seen = new WeakMap();
    for (var i = 0; i < figures.length; i++) {
      var el = figures[i];
      seen.set(el, true);
      if (!shown.get(el) && POSES[who(el)] && t - (lastGreet.get(el) || -Infinity) >= GREET_GAP_MS) greetDue.set(el, true);
      shown.set(el, true);
    }
    var all = document.querySelectorAll('.cw-figure-body');
    for (var j = 0; j < all.length; j++) if (!seen.get(all[j])) shown.delete(all[j]);
  }
  function greet(el) {
    var name = who(el);
    if (!greetDue.get(el) || !posesOn(name)) return 0;
    var ms = posePlay(el, POSES[name].greet, 'greet');
    if (ms) { greetDue.delete(el); lastGreet.set(el, now()); }
    return ms;
  }

  // ─── Idle behaviours ──────────────────────────────────────────────────────
  function standing() {
    var out = [], nodes = document.querySelectorAll(FIGURE);
    for (var i = 0; i < nodes.length; i++) if (nodes[i].offsetParent !== null) out.push(nodes[i]);
    return out;
  }
  function conversationActive() {
    return !!document.querySelector(OPEN_CHAT) || !!document.querySelector(PENDING) ||
      now() - lastTyped < TYPING_QUIET_MS;
  }
  // The agent of THIS figure has a task running or a reply on the way
  function working(el) {
    var win = el && el.closest ? el.closest('.chat-window') : null;
    return !!(win && win.querySelector && win.querySelector(WORKING));
  }
  function pick(weighted) {
    var total = 0, i;
    for (i = 0; i < weighted.length; i++) total += weighted[i][1];
    var roll = Math.random() * total;
    for (i = 0; i < weighted.length; i++) { roll -= weighted[i][1]; if (roll <= 0) return weighted[i]; }
    return weighted[0];
  }
  function still(el) {
    if (!el || !el.classList) return;
    for (var name in MOVES) el.classList.remove('al-' + name);
    var emote = el.querySelector ? el.querySelector('.al-emote') : null;
    if (emote) { emote.classList.remove('al-emote-on'); emote.textContent = ''; }
  }
  // One behaviour, once. Returns how long it plays (0 = nothing played).
  function play(el, move, dir, bubble) {
    if (!el || !el.isConnected || !MOVES[move]) return 0;
    var rig = rigOf(el);
    if (!rig && !WHOLE[move]) return 0;
    still(el);
    el.style.setProperty('--al-dir', dir < 0 ? '-1' : '1');
    void el.offsetWidth;                               // one read of ONE element: restarts the same class cleanly
    el.classList.add('al-' + move);
    var ms = MOVES[move], emote = rig && bubble && rig.querySelector ? rig.querySelector('.al-emote') : null;
    if (emote) {
      emote.textContent = bubble;
      emote.style.animationDuration = Math.max(ms, 1800) + 'ms';
      emote.classList.add('al-emote-on');
    }
    var turn = (plays.get(el) || 0) + 1;
    plays.set(el, turn);
    // a timer, not animationend: the layers' own animations bubble up and would end it early
    wait(function () { if (plays.get(el) === turn) still(el); }, Math.max(ms, emote ? 1800 : 0) + 80);
    busy.set(el, now() + ms + between(MIN_REST, MAX_REST));
    lastMove.set(el, move);
    return ms;
  }
  function oneOf(text) { var all = String(text || '').split(' '); return all[Math.floor(Math.random() * all.length)]; }
  function solo(el) {
    var name = who(el);
    if (POSES[name]) {
      if (posesOn(name)) return poseSolo(el, name);
      var stage = stageOf(el, false);                  // switched off (or a frame failed): today's figure
      if (stage) { plays.set(el, (plays.get(el) || 0) + 1); drop(stage); }
    }
    var cast = CAST[name] || CAST.any, rigged = !!rigOf(el), last = lastMove.get(el);
    var options = cast.filter(function (c) { return c[0] !== last && (rigged || WHOLE[c[0]]); });
    if (!options.length) options = CAST.any.filter(function (c) { return WHOLE[c[0]]; });
    var choice = pick(options);
    return play(el, choice[0], Math.random() < 0.5 ? -1 : 1, choice[2] ? oneOf(choice[2]) : '');
  }
  function neighbour(el, figures) {
    var best = null, bestGap = PAIR_REACH, me = el.getBoundingClientRect();
    for (var i = 0; i < figures.length; i++) {
      if (figures[i] === el) continue;
      var r = figures[i].getBoundingClientRect(), gap = Math.abs((r.left + r.width / 2) - (me.left + me.width / 2));
      if (gap < bestGap) { bestGap = gap; best = { el: figures[i], dir: r.left > me.left ? 1 : -1 }; }
    }
    return best;
  }
  function quiet() { return allowed() && !conversationActive(); }
  // Two side by side: one starts, the other answers, one closing beat
  function together(a, other) {
    if (!rigOf(a) || !rigOf(other.el)) return false;    // a glance needs a head that turns
    var scene = SCENES[Math.floor(Math.random() * SCENES.length)], b = other.el, toB = other.dir;
    if (!play(a, scene.a[0], toB, scene.a[1])) return false;
    busy.set(b, now() + scene.gap + 6000);              // b is spoken to: no solo behaviour meanwhile
    wait(function () { if (quiet() && !working(b)) play(b, scene.b[0], -toB, scene.b[1]); }, scene.gap);
    var end = scene.then, closer = end[0] === 'a' ? a : b;
    wait(function () {
      if (quiet() && !working(closer)) play(closer, end[1], end[0] === 'a' ? toB : -toB, end[3]);
    }, end[2] + scene.gap);
    busy.set(a, now() + end[2] + scene.gap + 3000);
    return true;
  }
  function idleOnce() {
    var figures = standing();
    noteArrivals(figures);                             // even mid-conversation: an open window is not "standing"
    if (conversationActive()) return 0;
    var t = now(), free = figures.filter(function (f) { return (busy.get(f) || 0) <= t && !working(f); });
    if (!free.length) return 0;
    // a figure with drawn poses: its frames start loading now (preload), and
    // one that just arrived waves first
    for (var i = 0; i < free.length; i++) {
      if (!posesOn(who(free[i]))) continue;
      stageOf(free[i], true);
      var waved = greet(free[i]);
      if (waved) { lastFigure = free[i]; return waved; }
    }
    var pool = free.length > 1 ? free.filter(function (f) { return f !== lastFigure; }) : free;
    var el = pool[Math.floor(Math.random() * pool.length)];
    lastFigure = el;
    if (free.length > 1 && Math.random() < PAIR_CHANCE) {
      var other = neighbour(el, free);
      if (other && together(el, other)) return 1;
    }
    return solo(el);
  }
  function schedule() {
    clearTimeout(timer);
    if (!allowed()) { timer = null; return; }
    timer = setTimeout(function () {
      try { if (allowed()) idleOnce(); } catch (e) { /* a missed behaviour is nothing */ }
      schedule();
    }, between(MIN_TICK, MAX_TICK));
  }
  function stop() {
    clearTimeout(timer); timer = null;
    while (later.length) clearTimeout(later.pop());
    var nodes = document.querySelectorAll('.cw-figure-body');
    for (var i = 0; i < nodes.length; i++) {
      still(nodes[i]);
      plays.set(nodes[i], (plays.get(nodes[i]) || 0) + 1);
      poseRest(nodes[i], true);                        // a sequence in progress ends on the resting pose
    }
  }

  // ─── Celebrations: one burst per finished task, anywhere ─────────────────
  // celebrate(origin, key, opts) is called for EVERY task that completed
  // successfully (a card that finished, a connection made, an onboarding step,
  // a site built, an export ready) — not only when an agent stands waiting.
  //   once      a key is remembered BEFORE anything is drawn (this browser:
  //             localStorage, else the session, else this page) — a re-render,
  //             a poll or a reload never repeats it
  //   merged    tasks that finish together (within MERGE_MS of a burst's
  //             start) share that burst; opts.group = the same event reported
  //             twice (a connection and the onboarding step it completes)
  //   queued    a task that finishes while a burst is on screen gets ONE burst
  //             right after it — however many arrive, never two at once
  //   origin    the element it rises from; none or a hidden one = the bottom
  //             centre of the screen
  var MERGE_MS = 1500, GROUP_MS = 20000, GAP_MS = 450, DURATION = 2300, KEPT = 300;
  var memory = {}, burstStarted = 0, queued = null, waiting = null, groups = {};
  var BACK_MS = 700;                                   // after the tab is shown again, before the burst that waited
  var COLORS = ['#FF4C1F', '#FFC53D', '#3DD68C', '#4DA3FF', '#F5F5F5', '#C1272D'];

  function doneList() {
    var kinds = ['localStorage', 'sessionStorage'];
    for (var i = 0; i < kinds.length; i++) {
      var s = store(kinds[i]);
      try { if (s) return { s: s, list: JSON.parse(s.getItem(DONE_KEY) || '[]') || [] }; } catch (e) { /* try the next */ }
    }
    return { s: null, list: [] };
  }
  function celebrated(key) {
    if (memory[String(key)]) return true;
    try { return doneList().list.indexOf(String(key)) !== -1; } catch (e) { return false; }
  }
  function remember(key) {
    memory[String(key)] = true;
    try {
      var d = doneList();
      d.list.push(String(key));
      if (d.s) d.s.setItem(DONE_KEY, JSON.stringify(d.list.slice(-KEPT)));
    } catch (e) { /* then it is remembered for this page only (memory) */ }
  }

  function celebrate(origin, key, opts) {
    if (key != null) {
      if (celebrated(key)) return false;
      remember(key);                                   // remembered BEFORE drawing: never twice
    }
    if (!enabled() || reduced()) return false;         // switched off / reduced motion: skipped for good
    var t = now(), group = opts && opts.group;
    if (group) {
      if (t - (groups[group] || 0) < GROUP_MS) return false;   // the same event, already celebrated
      groups[group] = t;
    }
    if (document.hidden) {                             // finished while they were away: ONE burst when they are back
      waiting = { origin: origin };
      return true;
    }
    if (burstRunning) {
      if (t - burstStarted < MERGE_MS) return false;   // finished together: the burst on screen is theirs too
      queued = { origin: origin };                     // one more, right after — however many arrive
      return true;
    }
    return burst(origin);
  }

  function burst(origin) {
    var w = window.innerWidth, h = window.innerHeight, dpr = Math.min(window.devicePixelRatio || 1, 2);
    var rect = origin && origin.getBoundingClientRect ? origin.getBoundingClientRect() : null;
    var seen = rect && (rect.width || rect.height) && rect.bottom > 0 && rect.top < h;
    var canvas = document.createElement('canvas');
    canvas.className = 'al-burst'; canvas.setAttribute('aria-hidden', 'true');
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    var ctx = canvas.getContext('2d');
    if (!ctx) return false;
    ctx.scale(dpr, dpr);
    document.body.appendChild(canvas);
    burstRunning = true; burstStarted = now();

    // from the element when it is on screen, else from the bottom centre
    var ox = seen ? Math.min(Math.max(rect.left + rect.width / 2, 24), w - 24) : w / 2;
    var oy = seen ? Math.min(Math.max(rect.top + rect.height * 0.25, 40), h - 20) : h * 0.86;
    var count = w < 640 ? 42 : 76, parts = [];
    for (var i = 0; i < count; i++) {
      var angle = -Math.PI / 2 + (Math.random() - 0.5) * 1.9, speed = 5 + Math.random() * 7.5;
      parts.push({ x: ox, y: oy, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed,
                   size: 4 + Math.random() * 5, rot: Math.random() * 6.28, spin: (Math.random() - 0.5) * 0.4,
                   color: COLORS[i % COLORS.length], round: i % 5 === 0 });
    }
    var balloon = { x: ox, y: oy, r: w < 640 ? 15 : 19, sway: Math.random() * 6.28, color: COLORS[0] };
    var started = performance.now(), last = started, frame = 0;

    var over = false;
    function end() {
      if (over) return;
      over = true;
      burstRunning = false;
      if (frame) cancelAnimationFrame(frame);
      document.removeEventListener('visibilitychange', onHide);
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
      var next = queued;
      queued = null;
      // the one that waited: a short breath, then its own burst (not on a hidden tab)
      if (next && allowed()) setTimeout(function () { if (allowed() && !burstRunning) burst(next.origin); }, GAP_MS);
    }
    function onHide() { if (document.hidden) { if (queued) waiting = queued; queued = null; end(); } }
    document.addEventListener('visibilitychange', onHide);

    function draw(nowMs) {
      var t = nowMs - started;
      if (t >= DURATION || document.hidden) { end(); return; }
      // by the clock, not by the frame: the same flight on a slow phone as on a fast screen
      var dt = Math.min(Math.max((nowMs - last) / 16.67, 0), 3);
      last = nowMs;
      var fade = t > DURATION - 700 ? Math.max(0, (DURATION - t) / 700) : 1;
      ctx.clearRect(0, 0, w, h);
      for (var i = 0; i < parts.length; i++) {
        var p = parts[i];
        p.vy += 0.24 * dt; p.vx *= Math.pow(0.985, dt); p.x += p.vx * dt; p.y += p.vy * dt; p.rot += p.spin * dt;
        ctx.save();
        ctx.globalAlpha = fade;
        ctx.translate(p.x, p.y); ctx.rotate(p.rot);
        ctx.fillStyle = p.color;
        if (p.round) { ctx.beginPath(); ctx.arc(0, 0, p.size / 2, 0, 6.2832); ctx.fill(); }
        else ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
        ctx.restore();
      }
      // the balloon: rises, sways a little, its string trailing
      balloon.y -= 2.3 * dt; balloon.sway += 0.07 * dt;
      var bx = balloon.x + Math.sin(balloon.sway) * 9, by = balloon.y;
      ctx.save();
      ctx.globalAlpha = fade;
      ctx.strokeStyle = 'rgba(245,245,245,0.55)'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(bx, by + balloon.r * 1.25);
      ctx.quadraticCurveTo(bx + Math.sin(balloon.sway * 1.6) * 7, by + balloon.r * 2.3, bx, by + balloon.r * 3.4); ctx.stroke();
      ctx.fillStyle = balloon.color;
      ctx.beginPath(); ctx.ellipse(bx, by, balloon.r, balloon.r * 1.22, 0, 0, 6.2832); ctx.fill();
      ctx.beginPath(); ctx.moveTo(bx - 4, by + balloon.r * 1.2); ctx.lineTo(bx + 4, by + balloon.r * 1.2);
      ctx.lineTo(bx, by + balloon.r * 1.2 - 6); ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.35)';
      ctx.beginPath(); ctx.ellipse(bx - balloon.r * 0.35, by - balloon.r * 0.4, balloon.r * 0.2, balloon.r * 0.32, -0.5, 0, 6.2832); ctx.fill();
      ctx.restore();
      frame = requestAnimationFrame(draw);
    }
    frame = requestAnimationFrame(draw);
    // a window that draws no frames (covered, throttled) must not keep the burst open
    setTimeout(end, DURATION + 500);
    // an agent that stands waiting gives a small hop
    if (origin && origin.classList && origin.classList.contains('cw-figure-body')) play(origin, 'hop', 1);
    return true;
  }

  // A finished task inside a chat window: rise from the standing figure when
  // the window is minimised (the agent is waiting), else from the card itself.
  function celebrateFor(cardEl, key, opts) {
    var win = cardEl && cardEl.closest ? cardEl.closest('.chat-window') : null;
    var figure = win && win.classList.contains('minimized') ? win.querySelector('.cw-figure-body') : null;
    return celebrate(figure || cardEl || null, key, opts);
  }

  // ─── Switch ───────────────────────────────────────────────────────────────
  function setEnabled(on) {
    var s = store('localStorage');
    try { if (s) { if (on) s.removeItem(KEY); else s.setItem(KEY, 'off'); } } catch (e) { /* this page only */ }
    if (on) schedule(); else stop();
    return enabled();
  }

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) { stop(); return; }
    schedule();
    var back = waiting;
    waiting = null;
    if (back) setTimeout(function () { if (allowed() && !burstRunning) burst(back.origin); }, BACK_MS);
  });
  document.addEventListener('keydown', function () { lastTyped = Date.now(); }, true);
  if (window.matchMedia) {
    var mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    var onChange = function () { if (mq.matches) stop(); else schedule(); };
    if (mq.addEventListener) mq.addEventListener('change', onChange); else if (mq.addListener) mq.addListener(onChange);
  }

  window.uallakLife = { celebrate: celebrate, celebrateFor: celebrateFor, setEnabled: setEnabled, enabled: enabled,
                        reduced: reduced, posesOn: posesOn, _idleOnce: idleOnce, _pick: pick, _play: play, _who: who,
                        _cast: CAST, _moves: MOVES, _scenes: SCENES, _rig: RIG, _whole: WHOLE, _poses: POSES,
                        _fadeMs: FADE_MS };
  schedule();
})();
