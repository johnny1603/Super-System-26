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

   2. ONE CELEBRATION per finished task — confetti and a balloon.

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
  var DONE_KEY = 'uallak_life_done';                   // task keys already celebrated (session)
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

  var timer = null, lastFigure = null, lastTyped = 0, burstRunning = false, later = [];
  var busy = new WeakMap(), lastMove = new WeakMap(), plays = new WeakMap();

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
  function rigOf(el) {
    if (!el || !el.querySelector) return null;
    var rig = el.querySelector('.al-rig');
    if (rig) return rig;
    var pic = picture(el), spec = RIG[who(el)];
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
    pic.holder.parentNode.insertBefore(rig, pic.holder);
    return rig;
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
    var cast = CAST[who(el)] || CAST.any, rigged = !!rigOf(el), last = lastMove.get(el);
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
    if (conversationActive()) return 0;
    var t = now(), figures = standing();
    var free = figures.filter(function (f) { return (busy.get(f) || 0) <= t && !working(f); });
    if (!free.length) return 0;
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
    for (var i = 0; i < nodes.length; i++) still(nodes[i]);
  }

  // ─── The one celebration ──────────────────────────────────────────────────
  function celebrated(key) {
    var s = store('sessionStorage');
    try { return (JSON.parse((s && s.getItem(DONE_KEY)) || '[]')).indexOf(String(key)) !== -1; } catch (e) { return false; }
  }
  function remember(key) {
    var s = store('sessionStorage');
    try {
      var list = JSON.parse((s && s.getItem(DONE_KEY)) || '[]');
      list.push(String(key));
      if (s) s.setItem(DONE_KEY, JSON.stringify(list.slice(-200)));
    } catch (e) { /* then it is remembered for this page only (memory) */ }
    memory[String(key)] = true;
  }
  var memory = {};
  var COLORS = ['#FF4C1F', '#FFC53D', '#3DD68C', '#4DA3FF', '#F5F5F5', '#C1272D'];

  // origin: the element the burst rises from (the agent's figure when it is
  // standing, else the finished card). key: one celebration per key, ever.
  function celebrate(origin, key) {
    if (key != null) {
      if (memory[String(key)] || celebrated(key)) return false;
      remember(key);                                   // remembered BEFORE drawing: never twice
    }
    if (!allowed() || burstRunning || !origin || !origin.getBoundingClientRect) return false;
    var rect = origin.getBoundingClientRect();
    if (!rect.width && !rect.height) return false;
    var w = window.innerWidth, h = window.innerHeight, dpr = Math.min(window.devicePixelRatio || 1, 2);
    var canvas = document.createElement('canvas');
    canvas.className = 'al-burst'; canvas.setAttribute('aria-hidden', 'true');
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    var ctx = canvas.getContext('2d');
    if (!ctx) return false;
    ctx.scale(dpr, dpr);
    document.body.appendChild(canvas);
    burstRunning = true;

    var ox = Math.min(Math.max(rect.left + rect.width / 2, 24), w - 24), oy = Math.max(rect.top + rect.height * 0.25, 40);
    var count = w < 640 ? 42 : 76, parts = [];
    for (var i = 0; i < count; i++) {
      var angle = -Math.PI / 2 + (Math.random() - 0.5) * 1.9, speed = 5 + Math.random() * 7.5;
      parts.push({ x: ox, y: oy, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed,
                   size: 4 + Math.random() * 5, rot: Math.random() * 6.28, spin: (Math.random() - 0.5) * 0.4,
                   color: COLORS[i % COLORS.length], round: i % 5 === 0 });
    }
    var balloon = { x: ox, y: oy, r: w < 640 ? 15 : 19, sway: Math.random() * 6.28, color: COLORS[0] };
    var started = performance.now(), DURATION = 2600, frame = 0;

    function end() {
      burstRunning = false;
      if (frame) cancelAnimationFrame(frame);
      document.removeEventListener('visibilitychange', onHide);
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
    }
    function onHide() { if (document.hidden) end(); }
    document.addEventListener('visibilitychange', onHide);

    function draw(now) {
      var t = now - started;
      if (t >= DURATION || document.hidden) { end(); return; }
      var fade = t > DURATION - 700 ? Math.max(0, (DURATION - t) / 700) : 1;
      ctx.clearRect(0, 0, w, h);
      for (var i = 0; i < parts.length; i++) {
        var p = parts[i];
        p.vy += 0.24; p.vx *= 0.985; p.x += p.vx; p.y += p.vy; p.rot += p.spin;
        ctx.save();
        ctx.globalAlpha = fade;
        ctx.translate(p.x, p.y); ctx.rotate(p.rot);
        ctx.fillStyle = p.color;
        if (p.round) { ctx.beginPath(); ctx.arc(0, 0, p.size / 2, 0, 6.2832); ctx.fill(); }
        else ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
        ctx.restore();
      }
      // the balloon: rises, sways a little, its string trailing
      balloon.y -= 2.3; balloon.sway += 0.07;
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
    // the agent itself gives a small hop
    if (origin.classList && origin.classList.contains('cw-figure-body')) play(origin, 'hop', 1);
    return true;
  }

  // A finished task inside a chat window: rise from the standing figure when
  // the window is minimised (the agent is waiting), else from the card itself.
  function celebrateFor(cardEl, key) {
    if (!cardEl) return false;
    var win = cardEl.closest ? cardEl.closest('.chat-window') : null;
    var figure = win && win.classList.contains('minimized') ? win.querySelector('.cw-figure-body') : null;
    return celebrate(figure || cardEl, key);
  }

  // ─── Switch ───────────────────────────────────────────────────────────────
  function setEnabled(on) {
    var s = store('localStorage');
    try { if (s) { if (on) s.removeItem(KEY); else s.setItem(KEY, 'off'); } } catch (e) { /* this page only */ }
    if (on) schedule(); else stop();
    return enabled();
  }

  document.addEventListener('visibilitychange', function () { if (document.hidden) stop(); else schedule(); });
  document.addEventListener('keydown', function () { lastTyped = Date.now(); }, true);
  if (window.matchMedia) {
    var mq = window.matchMedia('(prefers-reduced-motion: reduce)');
    var onChange = function () { if (mq.matches) stop(); else schedule(); };
    if (mq.addEventListener) mq.addEventListener('change', onChange); else if (mq.addListener) mq.addListener(onChange);
  }

  window.uallakLife = { celebrate: celebrate, celebrateFor: celebrateFor, setEnabled: setEnabled, enabled: enabled,
                        reduced: reduced, _idleOnce: idleOnce, _pick: pick, _play: play, _who: who,
                        _cast: CAST, _moves: MOVES, _scenes: SCENES, _rig: RIG, _whole: WHOLE };
  schedule();
})();
