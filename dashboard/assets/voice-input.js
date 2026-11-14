/* voice-input.js — speak a chat message instead of typing it
   (docs/VOICE-INPUT.md, 2026-11-15). Client dashboard chat windows.

   The client taps the microphone, speaks, and the words appear in the input
   box as TEXT. Nothing is sent: they read it, fix what they like, and send it
   like any typed message. From the server's side a voice message does not
   exist — it receives ordinary text through the ordinary chat route, with
   every existing check.

   HOW THE SPEECH BECOMES TEXT: the browser's own speech recognition
   (SpeechRecognition / webkitSpeechRecognition). No service of ours, no key,
   no cost.
   - This script never opens the microphone itself (no getUserMedia, no
     MediaRecorder), never holds an audio stream and never uploads anything:
     uallak neither receives nor stores the recording.
   - The audio is processed by the BROWSER's speech service (Google for
     Chrome, Apple for Safari, Microsoft for Edge) under the browser's own
     permission prompt. docs/SYSTEM-REVIEW.md carries the TODO that the
     privacy policy must say so.
   - Where the browser has no speech recognition (Firefox, some in-app
     browsers) the microphone button is simply not drawn.

   BEHAVIOUR
   - One recording at a time. A strip shows a dot, the elapsed time, Cancel and
     Done. Cancel restores the box exactly as it was. Done keeps the text and
     puts the cursor in the box.
   - At most MAX_SECONDS; then it stops by itself and says so.
   - Browsers end a recognition on a pause (iOS Safari always, Android Chrome
     by design): while the client is still recording it is restarted quietly
     and the text carries on.
   - Microphone refused / missing / service unreachable: ONE calm line above
     the box, the button stays, typing always works. Never a raw error.
   - A hidden tab, a sent message, a minimised or closed window stop the
     recording at once.
   - Never re-parents a node: the button and the strip are built with the
     window and shown / hidden with a class.
*/
(function () {
  'use strict';
  if (window.uallakVoice) return;

  var SR = window.SpeechRecognition || window.webkitSpeechRecognition || null;
  var MAX_SECONDS = 120;
  var FINAL_WAIT_MS = 1500;              // after Done: how long to wait for the last words
  var NOTE_MS = 7000;
  // The UI language -> the locales to ask the recogniser for, in order. Arabic
  // tries the Israeli locale first and falls back where a browser lacks it.
  var LOCALES = { he: ['he-IL'], en: ['en-US'], fr: ['fr-FR'], ar: ['ar-IL', 'ar-SA', 'ar'], ru: ['ru-RU'] };
  var ANDROID = /Android/i.test((window.navigator && window.navigator.userAgent) || '');

  var CSS = [
    '.cw-input-area{position:relative;}',
    '.cw-mic{width:36px;height:36px;border-radius:50%;border:1px solid var(--border);background:var(--panel-2);',
    'color:inherit;cursor:pointer;flex-shrink:0;display:flex;align-items:center;justify-content:center;padding:0;',
    'font-family:inherit;-webkit-tap-highlight-color:transparent;}',
    '.cw-mic:hover{border-color:var(--accent);color:var(--accent);}',
    '.cw-mic:focus-visible,.cw-rec button:focus-visible{outline:2px solid var(--accent);outline-offset:2px;}',
    '.cw-rec{display:none;align-items:center;gap:6px;flex-shrink:0;}',
    '.cw-input-area.recording .cw-rec{display:flex;}',
    '.cw-input-area.recording .cw-mic,.cw-input-area.recording .cw-attach,.cw-input-area.recording .support-send{display:none;}',
    '.cw-rec-dot{width:9px;height:9px;border-radius:50%;background:#E5484D;flex-shrink:0;}',
    '.cw-rec-time{font-size:12.5px;font-variant-numeric:tabular-nums;color:var(--gray);min-width:36px;text-align:center;direction:ltr;}',
    '.cw-rec button{height:36px;min-width:36px;border-radius:18px;border:1px solid var(--border);background:var(--panel-2);',
    'color:inherit;cursor:pointer;font-family:inherit;font-size:12.5px;font-weight:700;padding:0 11px;}',
    '.cw-rec button[data-voice="done"]{background:var(--accent);border-color:var(--accent);color:#fff;}',
    '.cw-voice-note{position:absolute;inset-inline:10px;bottom:calc(100% + 6px);z-index:2;display:none;font-size:12px;',
    'line-height:1.45;background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:7px 10px;',
    'box-shadow:0 6px 18px rgba(0,0,0,0.35);}',
    '.cw-voice-note.show{display:block;}',
    '@media (prefers-reduced-motion: no-preference){.cw-input-area.recording .cw-rec-dot{animation:cwRecPulse 1.2s ease-in-out infinite;}',
    '@keyframes cwRecPulse{0%,100%{opacity:1;}50%{opacity:0.35;}}}'
  ].join('');
  var MIC_SVG = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3"/>' +
    '<path d="M5 11a7 7 0 0 0 14 0"/><line x1="12" y1="18" x2="12" y2="22"/></svg>';

  var styled = false, active = null;

  function supported() { return !!SR; }
  function clock(seconds) {
    var s = Math.max(0, Math.floor(seconds));
    return Math.floor(s / 60) + ':' + (s % 60 < 10 ? '0' : '') + (s % 60);
  }
  function joinText(base, spoken) {
    base = String(base || ''); spoken = String(spoken || '').replace(/\s+/g, ' ').trim();
    if (!spoken) return base;
    return base && !/\s$/.test(base) ? base + ' ' + spoken : base + spoken;
  }
  // Which calm line a recogniser error becomes ('' = nothing to say)
  function errorKey(code) {
    if (code === 'not-allowed' || code === 'service-not-allowed') return 'voice_denied';
    if (code === 'audio-capture') return 'voice_no_mic';
    if (code === 'network') return 'voice_network';
    if (code === 'no-speech' || code === 'aborted') return '';
    return 'voice_failed';
  }

  function attach(opts) {
    // opts: area (the input row), input (textarea), before (the send button),
    //       t(key) -> text, lang() -> 'he'|'en'|…, setText(text)
    if (!SR || !opts || !opts.area || !opts.input) return null;
    if (!styled) {
      var style = document.createElement('style');
      style.id = 'voice-input-css'; style.textContent = CSS; document.head.appendChild(style); styled = true;
    }
    var t = opts.t || function (k) { return k; };
    var mic = document.createElement('button');
    mic.type = 'button'; mic.className = 'cw-mic'; mic.innerHTML = MIC_SVG;
    var strip = document.createElement('div');
    strip.className = 'cw-rec'; strip.setAttribute('role', 'group');
    var dot = document.createElement('span'); dot.className = 'cw-rec-dot'; dot.setAttribute('aria-hidden', 'true');
    var time = document.createElement('span'); time.className = 'cw-rec-time'; time.textContent = '0:00';
    var cancelBtn = document.createElement('button'); cancelBtn.type = 'button'; cancelBtn.dataset.voice = 'cancel';
    var doneBtn = document.createElement('button'); doneBtn.type = 'button'; doneBtn.dataset.voice = 'done';
    strip.appendChild(dot); strip.appendChild(time); strip.appendChild(cancelBtn); strip.appendChild(doneBtn);
    var note = document.createElement('div');
    note.className = 'cw-voice-note'; note.setAttribute('role', 'status'); note.setAttribute('aria-live', 'polite');
    // Built once, here, before the window is shown; only classes change later
    if (opts.before && opts.before.parentNode === opts.area) opts.area.insertBefore(mic, opts.before);
    else opts.area.appendChild(mic);
    opts.area.appendChild(strip); opts.area.appendChild(note);

    var ctx = { opts: opts, area: opts.area, input: opts.input, mic: mic, time: time, note: note, t: t,
                rec: null, state: 'idle', base: '', committed: '', session: '', interim: '',
                startedAt: 0, ticker: null, noteTimer: null, finalTimer: null, localeIndex: 0, problem: '' };

    function label() {
      mic.title = t('voice_start'); mic.setAttribute('aria-label', t('voice_start'));
      cancelBtn.textContent = t('voice_cancel'); doneBtn.textContent = t('voice_done');
      strip.setAttribute('aria-label', t('voice_listening'));
    }
    label();
    ctx.relabel = label;
    mic.addEventListener('click', function () { start(ctx); });
    cancelBtn.addEventListener('click', function () { cancel(ctx); });
    doneBtn.addEventListener('click', function () { done(ctx); });
    return { relabel: label, stop: function () { stopNow(ctx); }, recording: function () { return ctx.state !== 'idle'; } };
  }

  function say(ctx, key) {
    clearTimeout(ctx.noteTimer);
    if (!key) { ctx.note.classList.remove('show'); return; }
    ctx.note.textContent = ctx.t(key);
    ctx.note.classList.add('show');
    ctx.noteTimer = setTimeout(function () { ctx.note.classList.remove('show'); }, NOTE_MS);
  }
  function render(ctx) {
    if (ctx.state === 'idle') return;                  // closed: late results never touch the box
    ctx.opts.setText(joinText(ctx.base, ctx.committed + ' ' + ctx.session + ' ' + ctx.interim));
  }

  function listen(ctx) {
    var locales = LOCALES[(ctx.opts.lang && ctx.opts.lang()) || 'he'] || LOCALES.en;
    var rec = new SR();
    rec.lang = locales[Math.min(ctx.localeIndex, locales.length - 1)];
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    // Android Chrome repeats earlier words in continuous mode: one phrase per
    // session there, restarted on end (as iOS does by itself)
    rec.continuous = !ANDROID;
    rec.onresult = function (e) {
      if (ctx.rec !== rec) return;
      var finals = '', interim = '';
      for (var i = 0; i < e.results.length; i++) {
        var piece = e.results[i][0] ? e.results[i][0].transcript : '';
        if (e.results[i].isFinal) finals += piece + ' '; else interim += piece;
      }
      ctx.session = finals; ctx.interim = interim;
      render(ctx);
    };
    rec.onerror = function (e) {
      if (ctx.rec !== rec) return;
      var code = (e && e.error) || '';
      if (code === 'language-not-supported' && ctx.localeIndex < locales.length - 1) { ctx.localeIndex += 1; return; }
      var key = errorKey(code);
      if (key) { ctx.problem = key; }                  // fatal for this recording: no quiet restart
    };
    rec.onend = function () {
      if (ctx.rec !== rec) return;
      ctx.committed = (ctx.committed + ' ' + ctx.session).trim(); ctx.session = ''; ctx.interim = '';
      render(ctx);
      if (ctx.state === 'recording' && !ctx.problem && (Date.now() - ctx.startedAt) / 1000 < MAX_SECONDS
          && !document.hidden) {
        // a pause ended the session; the client is still recording
        setTimeout(function () { if (ctx.state === 'recording' && ctx.rec === rec) listen(ctx); }, 150);
        return;
      }
      finish(ctx, true);
    };
    ctx.rec = rec;
    try { rec.start(); }
    catch (err) { ctx.problem = ctx.problem || 'voice_failed'; finish(ctx, true); }
  }

  function start(ctx) {
    if (ctx.state !== 'idle') return;
    if (active && active !== ctx) stopNow(active);     // one recording at a time
    active = ctx;
    say(ctx, '');
    ctx.base = ctx.input.value || '';
    ctx.committed = ctx.session = ctx.interim = ''; ctx.problem = ''; ctx.limit = false; ctx.localeIndex = 0;
    ctx.state = 'recording'; ctx.startedAt = Date.now();
    ctx.area.classList.add('recording');
    ctx.time.textContent = '0:00';
    ctx.ticker = setInterval(function () {
      var elapsed = (Date.now() - ctx.startedAt) / 1000;
      ctx.time.textContent = clock(elapsed);
      if (elapsed >= MAX_SECONDS) { ctx.limit = true; done(ctx); }
    }, 500);
    listen(ctx);
  }

  // Done: let the recogniser deliver the last words, then close
  function done(ctx) {
    if (ctx.state !== 'recording') return;
    ctx.state = 'stopping';
    clearInterval(ctx.ticker);
    try { ctx.rec && ctx.rec.stop(); } catch (e) { /* closing anyway */ }
    ctx.finalTimer = setTimeout(function () { finish(ctx, true); }, FINAL_WAIT_MS);
  }

  // Cancel: the box goes back to exactly what it held before
  function cancel(ctx) {
    if (ctx.state === 'idle') return;
    var base = ctx.base;
    close(ctx);
    ctx.opts.setText(base);
    try { ctx.input.focus(); } catch (e) { /* fine */ }
  }

  // Stop at once, keeping whatever text is already in the box (a message was
  // sent, the window was minimised, the tab was hidden)
  function stopNow(ctx) { if (ctx.state !== 'idle') close(ctx); }

  function close(ctx) {
    var rec = ctx.rec;
    ctx.rec = null; ctx.state = 'idle';
    clearInterval(ctx.ticker); clearTimeout(ctx.finalTimer);
    ctx.area.classList.remove('recording');
    if (active === ctx) active = null;
    try { rec && rec.abort(); } catch (e) { /* already ended */ }
  }

  function finish(ctx, keep) {
    if (ctx.state === 'idle') return;
    var spoken = (ctx.committed + ' ' + ctx.session).trim(), problem = ctx.problem, limit = ctx.limit;
    if (keep) { ctx.interim = ''; render(ctx); }
    close(ctx);
    if (problem) say(ctx, problem);
    else if (!spoken) say(ctx, 'voice_nothing');
    else say(ctx, limit ? 'voice_limit' : 'voice_review');
    try { ctx.input.focus(); } catch (e) { /* fine */ }
  }

  document.addEventListener('visibilitychange', function () { if (document.hidden && active) stopNow(active); });

  window.uallakVoice = { supported: supported, attach: attach, stop: function () { if (active) stopNow(active); },
                         MAX_SECONDS: MAX_SECONDS, _clock: clock, _join: joinText, _errorKey: errorKey };
})();
