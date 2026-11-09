/* Page-driven warm-up (core/warmup, docs/WARMUP.md).

   Loaded by the client dashboard and the admin pages. It keeps the Cloud Run
   instance awake while a page is open and VISIBLE — no min instances, no
   --no-cpu-throttling, no scheduler job:
   - pings GET /api/warmup/ at once on load (before the client types);
   - every `warmup_interval_seconds` (admin setting via /api/warmup/config,
     default 240) while document.visibilityState === 'visible'; the interval
     stops when the tab is hidden or closed;
   - pings at once when the tab becomes visible again. If the server's
     version differs from the one this page was loaded with, the page
     reloads — but never while the client is typing, has an unsent draft, or
     an upload / a send is in progress (window.__uallakBusy > 0): it waits
     until the chat is idle. One reload per new version per tab
     (sessionStorage), so a rollout with old and new instances side by side
     can never loop.
   Silent: no spinner, no message to the client; failures are ignored (the
   next interval tries again) and only console.debug ever logs. */
(function () {
  'use strict';
  if (window.UallakWarmup) return;

  const PING = '/api/warmup/';
  const CONFIG = '/api/warmup/config';
  const DEFAULT_S = 240;
  const IDLE_CHECK_MS = 2000;
  const RELOADED_KEY = 'uallak_warmup_reloaded_for';

  let intervalMs = DEFAULT_S * 1000;
  let timer = null;
  let loadedVersion = null;     // the version this page was loaded with (first answer)
  let pendingVersion = null;    // a newer version seen; reload when idle
  let idleTimer = null;

  const debug = (...a) => { try { console.debug('[warmup]', ...a); } catch (e) { /* none */ } };
  const visible = () => document.visibilityState === 'visible';

  async function ping() {
    try {
      const r = await fetch(PING, { credentials: 'omit', cache: 'no-store' });
      if (!r.ok) { debug('ping', r.status); return null; }
      const d = await r.json();
      const v = d && d.version ? String(d.version) : null;
      if (v && loadedVersion === null) loadedVersion = v;
      return v;
    } catch (e) { debug('ping failed'); return null; }
  }

  function stop() { if (timer) { clearInterval(timer); timer = null; } }
  function start() {
    stop();
    if (visible()) timer = setInterval(() => { if (visible()) ping(); else stop(); }, intervalMs);
  }

  // ── "the chat is idle" ──
  function typingField(el) {
    if (!el) return false;
    if (el.isContentEditable) return true;
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'textarea') return true;
    if (tag !== 'input') return false;
    return ['text', 'search', 'email', 'url', 'tel', 'number', 'password', ''].includes((el.type || '').toLowerCase());
  }
  function busy() {
    if ((window.__uallakBusy || 0) > 0) return true;                 // upload / send in progress
    if (typingField(document.activeElement)) return true;            // typing right now
    const areas = document.querySelectorAll('textarea');
    for (let i = 0; i < areas.length; i++) if ((areas[i].value || '').trim()) return true;   // unsent draft
    return false;
  }

  function alreadyReloadedFor(v) {
    try { return sessionStorage.getItem(RELOADED_KEY) === v; } catch (e) { return false; }
  }
  function reloadWhenIdle() {
    if (!pendingVersion || idleTimer) return;
    const attempt = () => {
      idleTimer = null;
      if (!pendingVersion) return;
      if (!visible() || busy()) { idleTimer = setTimeout(attempt, IDLE_CHECK_MS); return; }
      try { sessionStorage.setItem(RELOADED_KEY, pendingVersion); } catch (e) { /* private mode */ }
      debug('new version', pendingVersion, '- reloading');
      window.location.reload();
    };
    attempt();
  }

  async function onVisible() {
    const v = await ping();
    start();
    if (v && loadedVersion && v !== loadedVersion && !alreadyReloadedFor(v)) {
      pendingVersion = v;
      reloadWhenIdle();
    }
  }

  document.addEventListener('visibilitychange', () => { if (visible()) onVisible(); else stop(); });
  window.addEventListener('pagehide', stop);

  async function boot() {
    ping();                                   // at once, before anything else
    try {
      const r = await fetch(CONFIG, { credentials: 'omit', cache: 'no-store' });
      if (r.ok) {
        const d = await r.json();
        const s = Number(d && d.interval_seconds);
        if (s >= 60 && s <= 3600) intervalMs = s * 1000;
        if (d && d.version && loadedVersion === null) loadedVersion = String(d.version);
      }
    } catch (e) { debug('config failed - default interval'); }
    start();
  }

  window.UallakWarmup = { ping, busy, _state: () => ({ intervalMs, loadedVersion, pendingVersion, running: !!timer }) };
  boot();
})();
