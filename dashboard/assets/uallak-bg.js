/* uallak animated background — thin triangle outlines in the brand orange (a
   few in the deeper red) rising from the bottom-right corner towards the upper
   left, fading out around the middle of the screen, then respawning.
   Speeds, sizes, counts and opacities are the tuned values from the design
   file — keep them behind the text; do not make them brighter or faster.

   - device pixel ratio capped at 2
   - stops completely while the tab is hidden, resumes when it returns
   - fewer shards on small screens
   - prefers-reduced-motion: one still frame, nothing animates
   Styles: /assets/uallak-bg.css. Loaded by the client dashboard, the landing,
   login and sales chat pages — never the admin dashboard. */
(function () {
  'use strict';
  if (window.__ukBg) return;
  window.__ukBg = true;

  var SHARDS = 22;          // desktop count (design file)
  var SHARDS_SMALL = 12;    // narrow screens
  var SMALL_WIDTH = 640;

  function start() {
    var layer = document.createElement('div');
    layer.className = 'uk-bg';
    layer.setAttribute('aria-hidden', 'true');
    var canvas = document.createElement('canvas');
    layer.appendChild(canvas);
    document.body.insertBefore(layer, document.body.firstChild);

    var ctx = canvas.getContext('2d');
    if (!ctx) return;
    var motion = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
    var w = 0, h = 0, dpr = 1, shards = [], raf = null, resizeTimer = null;

    function reduced() { return !!(motion && motion.matches); }

    function size() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = canvas.clientWidth; h = canvas.clientHeight;
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function spawn(seeded) {
      var r = 9 + Math.random() * 26;
      return {
        x: w * (0.72 + Math.random() * 0.45),
        y: seeded ? Math.random() * h : h * (1 + Math.random() * 0.35),
        r: r, rot: Math.random() * Math.PI * 2,
        spin: (Math.random() - 0.5) * 0.0045,
        vx: -(0.09 + Math.random() * 0.17),
        vy: -(0.13 + Math.random() * 0.24),
        hue: Math.random() < 0.25 ? '193,39,45' : '255,76,31'
      };
    }

    function reset() {
      size();
      var count = w < SMALL_WIDTH ? SHARDS_SMALL : SHARDS;
      shards = [];
      for (var i = 0; i < count; i++) shards.push(spawn(true));
    }

    function draw(move) {
      ctx.clearRect(0, 0, w, h);
      for (var i = 0; i < shards.length; i++) {
        var s = shards[i];
        if (move) { s.x += s.vx; s.y += s.vy; s.rot += s.spin; }
        // fade in from the corner, out by the middle of the screen
        var up = 1 - Math.min(1, Math.max(0, (s.y - h * 0.42) / (h * 0.55)));
        var a = Math.max(0, Math.min(1, 1 - up)) * 0.5;
        if (s.y < h * 0.3 || s.x < -60) { if (move) shards[i] = spawn(false); continue; }
        ctx.save();
        ctx.translate(s.x, s.y); ctx.rotate(s.rot);
        ctx.beginPath();
        ctx.moveTo(0, -s.r);
        ctx.lineTo(s.r * 0.87, s.r * 0.5);
        ctx.lineTo(-s.r * 0.87, s.r * 0.5);
        ctx.closePath();
        ctx.strokeStyle = 'rgba(' + s.hue + ',' + a + ')';
        ctx.lineWidth = 1.1;
        ctx.stroke();
        ctx.fillStyle = 'rgba(' + s.hue + ',' + (a * 0.13) + ')';
        ctx.fill();
        ctx.restore();
      }
    }

    function frame() {
      draw(true);
      raf = window.requestAnimationFrame(frame);
    }

    function stop() {
      if (raf) { window.cancelAnimationFrame(raf); raf = null; }
    }

    function run() {
      stop();
      if (reduced()) { draw(false); return; }   // a still picture, nothing moves
      if (!document.hidden) frame();
    }

    reset();
    run();

    window.addEventListener('resize', function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () { reset(); run(); }, 150);
    });
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) stop(); else run();
    });
    if (motion) {
      var onMotion = function () { run(); };
      if (motion.addEventListener) motion.addEventListener('change', onMotion);
      else if (motion.addListener) motion.addListener(onMotion);
    }
  }

  if (document.body) start();
  else document.addEventListener('DOMContentLoaded', start);
})();
