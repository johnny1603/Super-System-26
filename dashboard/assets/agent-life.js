/* agent-life.js — small signs of life for the agents' standing figures
   (docs/AGENT-LIFE.md, 2026-11-15). Client dashboard only.

   Two things, both light:

   1. IDLE MOTIONS. When agents stand waiting (minimised chat windows), one of
      them now and then breathes, shifts its weight, glances aside, nods, or
      leans toward its neighbour, who answers with a small nod. One motion at
      a time, a random figure, a random pause of several seconds between
      motions. CSS transforms on the figure only (agent-life.css) — this file
      just adds a class and removes it when the animation ends.
      NEVER while a conversation is active: any open chat window, a reply
      being awaited, or typing in the last few seconds stops them.

   2. ONE CELEBRATION. When a task finishes, a single burst of confetti and
      one balloon rise from the agent that did it — once per task, never
      repeated (remembered for the session). Drawn on one small-lived canvas
      that is removed when the burst ends; nothing runs in between.

   Rules that hold everywhere:
   - prefers-reduced-motion: nothing moves, nothing is drawn.
   - A hidden tab: no timer, no frame.
   - The client can turn it off (uallakLife.setEnabled(false), kept in
     localStorage) — the dashboard's "אזור אישי" has the switch.
   - No library, no network, no layout work: classes, transforms, one canvas.
   - Never moves a DOM node (the CLAUDE.md re-parenting trap): the canvas is
     appended to <body> and removed; figures only get / lose a class.
*/
(function () {
  'use strict';
  if (window.uallakLife) return;                       // one instance even if loaded twice

  var KEY = 'uallak_life';                             // 'off' = the client switched it off
  var DONE_KEY = 'uallak_life_done';                   // task keys already celebrated (session)
  var FIGURE = '.chat-window.minimized .cw-figure-body';
  var OPEN_CHAT = '.chat-window:not(.minimized)';
  var PENDING = '.chat-window .typing';                // a reply is being awaited
  var MIN_PAUSE = 4200, MAX_PAUSE = 10500;             // ms between two idle motions
  var TYPING_QUIET_MS = 6000;
  var CLASSES = ['al-breathe', 'al-shift', 'al-glance', 'al-nod', 'al-lean', 'al-hop'];
  // weight of each solo motion; the pair motion (lean + answer) is added when two stand together
  var SOLO = [['al-breathe', 40], ['al-shift', 22], ['al-glance', 18], ['al-nod', 12]];
  var PAIR_WEIGHT = 16;

  var timer = null, lastFigure = null, lastTyped = 0, burstRunning = false;

  function store(kind) { try { return window[kind]; } catch (e) { return null; } }
  function reduced() {
    return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  }
  function enabled() {
    var s = store('localStorage');
    try { return !(s && s.getItem(KEY) === 'off'); } catch (e) { return true; }
  }
  function allowed() { return enabled() && !reduced() && !document.hidden; }

  // ─── Idle motions ─────────────────────────────────────────────────────────
  function standing() {
    var out = [], nodes = document.querySelectorAll(FIGURE);
    for (var i = 0; i < nodes.length; i++) if (nodes[i].offsetParent !== null) out.push(nodes[i]);
    return out;
  }
  function conversationActive() {
    return !!document.querySelector(OPEN_CHAT) || !!document.querySelector(PENDING) ||
      Date.now() - lastTyped < TYPING_QUIET_MS;
  }
  function pick(weighted) {
    var total = 0, i;
    for (i = 0; i < weighted.length; i++) total += weighted[i][1];
    var roll = Math.random() * total;
    for (i = 0; i < weighted.length; i++) { roll -= weighted[i][1]; if (roll <= 0) return weighted[i][0]; }
    return weighted[0][0];
  }
  function play(el, cls, dir) {
    if (!el || !el.isConnected) return;
    for (var i = 0; i < CLASSES.length; i++) el.classList.remove(CLASSES[i]);
    el.style.setProperty('--al-dir', dir < 0 ? '-1' : '1');
    // restart cleanly if the same class was just there
    void el.offsetWidth;                               // one read of ONE element, on a multi-second cadence
    el.classList.add(cls);
    var done = function () { el.classList.remove(cls); el.removeEventListener('animationend', done); };
    el.addEventListener('animationend', done);
    setTimeout(done, 7000);                            // a safety net if animationend never fires
  }
  function neighbour(el, figures) {
    var best = null, bestGap = Infinity, me = el.getBoundingClientRect();
    for (var i = 0; i < figures.length; i++) {
      if (figures[i] === el) continue;
      var r = figures[i].getBoundingClientRect(), gap = Math.abs((r.left + r.width / 2) - (me.left + me.width / 2));
      if (gap < bestGap) { bestGap = gap; best = { el: figures[i], dir: r.left > me.left ? 1 : -1 }; }
    }
    return best;
  }
  function idleOnce() {
    var figures = standing();
    if (!figures.length || conversationActive()) return;
    var candidates = figures.length > 1 ? figures.filter(function (f) { return f !== lastFigure; }) : figures;
    var el = candidates[Math.floor(Math.random() * candidates.length)];
    lastFigure = el;
    var options = SOLO.slice();
    if (figures.length > 1) options.push(['pair', PAIR_WEIGHT]);
    var choice = pick(options);
    if (choice === 'pair') {
      var other = neighbour(el, figures);
      if (other) {
        play(el, 'al-lean', other.dir);
        setTimeout(function () { if (allowed() && !conversationActive()) play(other.el, 'al-nod', -other.dir); }, 900);
        return;
      }
      choice = 'al-glance';
    }
    play(el, choice, Math.random() < 0.5 ? -1 : 1);
  }
  function schedule() {
    clearTimeout(timer);
    if (!allowed()) { timer = null; return; }
    timer = setTimeout(function () {
      try { if (allowed()) idleOnce(); } catch (e) { /* a missed motion is nothing */ }
      schedule();
    }, MIN_PAUSE + Math.random() * (MAX_PAUSE - MIN_PAUSE));
  }
  function stop() {
    clearTimeout(timer); timer = null;
    var nodes = document.querySelectorAll('.cw-figure-body');
    for (var i = 0; i < nodes.length; i++) for (var c = 0; c < CLASSES.length; c++) nodes[i].classList.remove(CLASSES[c]);
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
    if (origin.classList && origin.classList.contains('cw-figure-body')) play(origin, 'al-hop', 1);
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
                        reduced: reduced, _idleOnce: idleOnce, _pick: pick };
  schedule();
})();
