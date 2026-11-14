/* home-v2.js — the homepage's second presentation layer (docs/HOMEPAGE-V2.md).

   Loaded (deferred) ONLY when the owner turned homepage_v2_enabled on. It adds
   motion to elements that already exist and never touches what the page says:
   no text, link, section or attribute a reader or a reviewer relies on is
   created, changed or removed. Without this file the page is complete.

   What it does:
   - a thin reading-progress line and a nav that settles after the first scroll;
   - a soft light in the hero and on cards that follows the pointer (desktop);
   - the three commitment figures pop once when they come into view.

   Rules: nothing runs under prefers-reduced-motion; scroll and pointer work is
   passive and coalesced into one animation frame; nothing runs on a hidden
   tab; no library, no network call, no storage. */
(function () {
  'use strict';
  if (window.__uallakHomeV2) return;
  window.__uallakHomeV2 = true;

  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduce) return;                                  // the CSS alone already holds the still page
  var fine = window.matchMedia && window.matchMedia('(hover: hover) and (pointer: fine)').matches;

  var nav = document.querySelector('.nav');
  var hero = document.querySelector('.hero');
  var root = document.documentElement;

  // The progress line: decoration only, hidden from assistive tech
  var bar = document.createElement('div');
  bar.className = 'v2-progress';
  bar.setAttribute('aria-hidden', 'true');
  document.body.appendChild(bar);

  var ticking = false;
  function onScrollFrame() {
    ticking = false;
    var max = root.scrollHeight - window.innerHeight;
    var y = window.pageYOffset || root.scrollTop || 0;
    bar.style.setProperty('--v2-progress', max > 0 ? Math.min(1, Math.max(0, y / max)).toFixed(4) : '0');
    if (nav) nav.classList.toggle('v2-scrolled', y > 24);
  }
  function onScroll() {
    if (ticking || document.hidden) return;
    ticking = true;
    requestAnimationFrame(onScrollFrame);
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll, { passive: true });
  onScrollFrame();

  // Pointer light — only where there is a real pointer
  if (fine) {
    var pending = null, lightTicking = false;
    var apply = function () {
      lightTicking = false;
      if (!pending) return;
      var el = pending.el, rect = el.getBoundingClientRect();
      el.style.setProperty(pending.x, (pending.cx - rect.left) + 'px');
      el.style.setProperty(pending.y, (pending.cy - rect.top) + 'px');
      pending = null;
    };
    var follow = function (el, x, y) {
      return function (e) {
        pending = { el: el, x: x, y: y, cx: e.clientX, cy: e.clientY };
        if (!lightTicking) { lightTicking = true; requestAnimationFrame(apply); }
      };
    };
    if (hero) {
      hero.addEventListener('pointermove', follow(hero, '--v2-hx', '--v2-hy'), { passive: true });
      hero.addEventListener('pointerenter', function () { hero.classList.add('v2-lit'); }, { passive: true });
      hero.addEventListener('pointerleave', function () { hero.classList.remove('v2-lit'); }, { passive: true });
    }
    var cards = document.querySelectorAll('.svc-card, .pain-card, .auto-card, .price-card');
    for (var i = 0; i < cards.length; i++) {
      cards[i].addEventListener('pointermove', follow(cards[i], '--v2-mx', '--v2-my'), { passive: true });
    }
  }

  // The commitment figures pop once as they come into view (class only — the
  // figures themselves, "72h" / "24/7" / "100%", are never rewritten)
  var stats = document.querySelectorAll('.commit-stat-num');
  if (stats.length && 'IntersectionObserver' in window) {
    var seen = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var index = Array.prototype.indexOf.call(stats, entry.target);
        entry.target.style.animationDelay = (Math.max(0, index) * 0.12) + 's';
        entry.target.classList.add('v2-pop');
        seen.unobserve(entry.target);
      });
    }, { threshold: 0.6 });
    for (var s = 0; s < stats.length; s++) seen.observe(stats[s]);
  }
})();
