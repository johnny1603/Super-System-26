/* uallak media viewer — the Media tab's lightbox + direct download, shared by
 * the client dashboard and the admin Control Room (server side:
 * core/drive_download.py).
 *
 *   uallakMediaViewer.open(items, index, opts)   lightbox over a folder's files
 *   uallakMediaViewer.download(item, opts, fmt)  check, then download one file
 *
 * opts = { fileUrl(id) -> endpoint path, t(key, vars) -> string,
 *          maxMb, notify(text), onUnauthorized() }
 *
 * Safety: every file name / link goes in through textContent or setAttribute,
 * never innerHTML. Images are shown only for raster types (item.previewable),
 * from Google's own thumbnail renderer first (=s1600, no load on our server)
 * and our inline endpoint only as a fallback. Videos play in Drive's own
 * preview player, loaded only when asked. The overlay is created once and only
 * ever touches its own nodes — never the chat windows or their inputs.
 */
(function () {
  'use strict';

  var ID_RE = /^[A-Za-z0-9_-]{10,200}$/;
  var ICONS = { image: '🖼️', video: '🎬', document: '📄', file: '📎' };
  var state = { items: [], index: 0, opts: null, el: null, lastFocus: null, touchX: null };

  function isRtl() { return document.documentElement.dir === 'rtl'; }

  function bigThumb(link) {
    // Google thumbnail URLs end in a size directive (=s220); ask for a large one
    return link ? link.replace(/=s\d+(-[a-z0-9-]+)?$/i, '') + '=s1600' : '';
  }

  function css() {
    if (document.getElementById('uallakMvCss')) return;
    var s = document.createElement('style');
    s.id = 'uallakMvCss';
    s.textContent = [
      '.mv-overlay{position:fixed;inset:0;z-index:9000;background:rgba(0,0,0,.88);display:none;flex-direction:column;}',
      '.mv-overlay.open{display:flex;}',
      '.mv-bar{display:flex;align-items:center;gap:8px;padding:10px 14px;color:#F7F4EF;font-family:inherit;flex-wrap:wrap;}',
      '.mv-name{flex:1 1 160px;min-width:0;font-size:14px;font-weight:700;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}',
      '.mv-count{font-size:12px;opacity:.7;}',
      '.mv-btn{background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.18);color:#F7F4EF;font:inherit;font-size:13px;',
      'font-weight:700;padding:8px 14px;border-radius:9px;cursor:pointer;text-decoration:none;white-space:nowrap;}',
      '.mv-btn:hover,.mv-btn:focus-visible{background:#FF4C1F;border-color:#FF4C1F;outline:none;}',
      '.mv-close{width:40px;height:40px;padding:0;border-radius:50%;font-size:18px;}',
      '.mv-stage{flex:1;position:relative;display:flex;align-items:center;justify-content:center;min-height:0;padding:0 56px 12px;}',
      '.mv-stage img{max-width:100%;max-height:100%;object-fit:contain;border-radius:6px;}',
      '.mv-stage iframe{width:min(100%,960px);height:min(100%,540px);border:0;border-radius:8px;background:#000;}',
      '.mv-media{position:relative;display:flex;align-items:center;justify-content:center;max-width:100%;max-height:100%;height:100%;}',
      '.mv-doc{color:#F7F4EF;text-align:center;display:flex;flex-direction:column;align-items:center;gap:12px;max-width:420px;}',
      '.mv-doc-ico{font-size:64px;opacity:.6;}',
      '.mv-play{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:15px;padding:12px 20px;',
      'background:rgba(0,0,0,.72);border-color:rgba(255,255,255,.45);}',
      '.mv-nav{position:absolute;top:50%;transform:translateY(-50%);width:44px;height:44px;border-radius:50%;padding:0;font-size:22px;}',
      '.mv-nav.prev{inset-inline-start:8px;}.mv-nav.next{inset-inline-end:8px;}',
      '.mv-note{color:#F7F4EF;font-size:13px;line-height:1.6;text-align:center;padding:0 16px 14px;min-height:20px;}',
      '.mv-note:empty{display:none;}',
      '@media (max-width:640px){.mv-stage{padding:0 8px 8px;}.mv-nav{top:auto;bottom:10px;transform:none;}',
      '.mv-bar{padding:8px 10px;}.mv-btn{padding:7px 11px;font-size:12.5px;}}'
    ].join('');
    document.head.appendChild(s);
  }

  function node(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function build() {
    css();
    var o = node('div', 'mv-overlay');
    o.setAttribute('role', 'dialog');
    o.setAttribute('aria-modal', 'true');
    var bar = node('div', 'mv-bar');
    var name = node('span', 'mv-name');
    name.setAttribute('dir', 'auto');
    var count = node('span', 'mv-count');
    count.setAttribute('dir', 'ltr');   // "1 / 3", not bidi-reordered to "3 / 1" in RTL
    var actions = node('span');
    actions.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;';
    var close = node('button', 'mv-btn mv-close', '✕');
    close.type = 'button';
    close.onclick = closeViewer;
    bar.appendChild(name); bar.appendChild(count); bar.appendChild(actions); bar.appendChild(close);
    var stage = node('div', 'mv-stage');
    var prev = node('button', 'mv-btn mv-nav prev');
    var next = node('button', 'mv-btn mv-nav next');
    prev.type = next.type = 'button';
    prev.onclick = function (e) { e.stopPropagation(); step(-1); };
    next.onclick = function (e) { e.stopPropagation(); step(1); };
    var note = node('div', 'mv-note');
    note.setAttribute('role', 'status');
    o.appendChild(bar); o.appendChild(stage); o.appendChild(note);
    // Click outside the content (the dimmed background) closes
    o.addEventListener('click', function (e) { if (e.target === o || e.target === stage) closeViewer(); });
    o.addEventListener('touchstart', function (e) { state.touchX = e.touches[0].clientX; }, { passive: true });
    o.addEventListener('touchend', function (e) {
      if (state.touchX == null) return;
      var dx = e.changedTouches[0].clientX - state.touchX;
      state.touchX = null;
      if (Math.abs(dx) > 50) step((dx < 0) !== isRtl() ? 1 : -1);
    });
    document.body.appendChild(o);
    state.el = { o: o, name: name, count: count, actions: actions, close: close, stage: stage,
                 prev: prev, next: next, note: note };
  }

  function onKey(e) {
    // stopPropagation: a page-level Esc handler (the admin drawer) must not
    // also fire and close what is underneath
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeViewer(); return; }
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      // "Next" follows reading direction: left in RTL, right in LTR
      e.preventDefault();
      step((e.key === 'ArrowRight') !== isRtl() ? 1 : -1);
      return;
    }
    if (e.key === 'Tab') {   // keep focus inside the dialog
      var f = Array.prototype.filter.call(state.el.o.querySelectorAll('button,a[href],iframe'),
                                          function (n) { return n.offsetParent !== null; });
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }

  function step(delta) {
    var n = state.items.length;
    if (n < 2) return;
    state.index = (state.index + delta + n) % n;
    render();
  }

  function tooBig(item, opts) {
    return item.download === 'file' && item.size && opts.maxMb && item.size > opts.maxMb * 1024 * 1024;
  }

  function actionButtons(item, opts, target, inViewer) {
    var t = opts.t;
    var notify = inViewer ? function (m) { state.el.note.textContent = m; } : opts.notify;
    if (item.web_view_link && /^https:\/\/(drive|docs)\.google\.com\//.test(item.web_view_link)) {
      var a = node('a', 'mv-btn', t('mv_open_drive'));
      a.href = item.web_view_link; a.target = '_blank'; a.rel = 'noopener noreferrer';
      target.appendChild(a);
    }
    if (item.download === 'none') return;
    if (tooBig(item, opts)) {
      if (inViewer) state.el.note.textContent = t('dl_too_large', { mb: opts.maxMb });
      return;
    }
    var formats = item.download === 'export' ? [['pdf', 'mv_download_pdf'], ['office', 'mv_download_office']]
                                             : [['', 'mv_download']];
    formats.forEach(function (f) {
      var b = node('button', 'mv-btn', t(f[1]));
      b.type = 'button';
      b.onclick = function (e) { e.stopPropagation(); download(item, opts, f[0], notify); };
      target.appendChild(b);
    });
  }

  function render() {
    var item = state.items[state.index], opts = state.opts, t = opts.t, el = state.el;
    el.name.textContent = item.name || '';
    el.o.setAttribute('aria-label', item.name || '');
    el.count.textContent = state.items.length > 1 ? (state.index + 1) + ' / ' + state.items.length : '';
    el.note.textContent = '';
    el.actions.textContent = '';
    actionButtons(item, opts, el.actions, true);
    el.close.setAttribute('aria-label', t('mv_close'));
    el.prev.setAttribute('aria-label', t('mv_prev'));
    el.next.setAttribute('aria-label', t('mv_next'));
    el.prev.textContent = isRtl() ? '›' : '‹';
    el.next.textContent = isRtl() ? '‹' : '›';

    el.stage.textContent = '';
    var multi = state.items.length > 1;
    if (item.previewable) {
      var img = node('img');
      img.alt = item.name || '';
      img.referrerPolicy = 'no-referrer';
      var fallback = opts.fileUrl(item.id) + '?inline=1';
      img.onerror = function () {
        if (img.dataset.fallback) { showDoc(item, t('mv_preview_failed')); return; }
        img.dataset.fallback = '1';
        img.src = fallback;
      };
      img.src = bigThumb(item.thumbnail_link) || fallback;
      if (!item.thumbnail_link) img.dataset.fallback = '1';
      el.stage.appendChild(img);
    } else if (item.kind === 'video' && ID_RE.test(item.id || '')) {
      var box = node('div', 'mv-media');
      if (item.thumbnail_link) {
        var poster = node('img');
        poster.alt = item.name || '';
        poster.referrerPolicy = 'no-referrer';
        poster.src = bigThumb(item.thumbnail_link);
        poster.onerror = function () { poster.remove(); };
        box.appendChild(poster);
      } else {
        box.appendChild(node('div', 'mv-doc-ico', ICONS.video));
      }
      var play = node('button', 'mv-btn mv-play', '▶ ' + t('mv_play_here'));
      play.type = 'button';
      play.onclick = function (e) {
        e.stopPropagation();
        // Drive's own player: the video streams from Google, not from us. It
        // plays when the viewer's browser is signed in to the Google account
        // the folder is shared with; the hint says what to do otherwise.
        var frame = node('iframe');
        frame.src = 'https://drive.google.com/file/d/' + item.id + '/preview';
        frame.allow = 'autoplay; fullscreen';
        frame.setAttribute('allowfullscreen', '');
        frame.setAttribute('referrerpolicy', 'no-referrer');
        frame.title = item.name || '';
        el.stage.textContent = '';
        el.stage.appendChild(frame);
        if (multi) { el.stage.appendChild(el.prev); el.stage.appendChild(el.next); }
        el.note.textContent = t('mv_video_hint');
      };
      box.appendChild(play);
      el.stage.appendChild(box);
    } else {
      showDoc(item, '');
    }
    if (multi) { el.stage.appendChild(el.prev); el.stage.appendChild(el.next); }
    preloadNeighbours();
  }

  function showDoc(item, message) {
    var el = state.el;
    el.stage.textContent = '';
    var d = node('div', 'mv-doc');
    d.appendChild(node('div', 'mv-doc-ico', ICONS[item.kind] || ICONS.file));
    var n = node('div', null, item.name || '');
    n.setAttribute('dir', 'auto');
    d.appendChild(n);
    el.stage.appendChild(d);
    if (state.items.length > 1) { el.stage.appendChild(el.prev); el.stage.appendChild(el.next); }
    if (message) el.note.textContent = message;
  }

  function preloadNeighbours() {
    [1, -1].forEach(function (d) {
      var it = state.items[(state.index + d + state.items.length) % state.items.length];
      if (it && it.previewable && it.thumbnail_link) {
        var i = new Image(); i.referrerPolicy = 'no-referrer'; i.src = bigThumb(it.thumbnail_link);
      }
    });
  }

  function open(items, index, opts) {
    if (!items || !items.length) return;
    if (!state.el) build();
    state.items = items;
    state.index = Math.max(0, Math.min(index || 0, items.length - 1));
    state.opts = opts;
    state.lastFocus = document.activeElement;
    state.el.o.classList.add('open');
    state.bodyOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    // On the document, capture phase: keys keep working after a click on the
    // (non-focusable) image moves focus to <body>
    document.addEventListener('keydown', onKey, true);
    render();
    state.el.close.focus();
  }

  function closeViewer() {
    if (!state.el || !state.el.o.classList.contains('open')) return;
    state.el.o.classList.remove('open');
    document.removeEventListener('keydown', onKey, true);
    state.el.stage.textContent = '';   // stops a playing video
    document.body.style.overflow = state.bodyOverflow || '';
    if (state.lastFocus && state.lastFocus.focus) { try { state.lastFocus.focus(); } catch (e) {} }
  }

  // Check first (ownership, size, rate — JSON), then let the browser download
  // the attachment. A failed check shows a calm sentence, never a code, and
  // never leaves a broken file in the downloads bar.
  var CODE_KEYS = { not_found: 'dl_gone', too_large: 'dl_too_large', busy: 'dl_busy',
                    not_downloadable: 'dl_not_downloadable', unavailable: 'dl_unavailable' };

  async function download(item, opts, fmt, notify) {
    var t = opts.t;
    notify = notify || opts.notify || function () {};
    if (!ID_RE.test(item.id || '')) { notify(t('dl_gone')); return; }
    var url = opts.fileUrl(item.id) + (fmt === 'office' ? '?format=office' : '');
    var checkUrl = url + (url.indexOf('?') === -1 ? '?' : '&') + 'check=1';
    var res, body = null;
    try {
      res = await fetch(checkUrl, { credentials: 'same-origin' });
      if (res.status === 401) { if (opts.onUnauthorized) opts.onUnauthorized(); return; }
      try { body = await res.json(); } catch (e) { body = null; }
    } catch (e) {
      notify(t('dl_unavailable'));
      return;
    }
    if (res.ok && body && body.success) {
      notify('');
      var a = document.createElement('a');
      a.href = url;
      a.rel = 'noopener';
      a.style.display = 'none';
      document.body.appendChild(a);   // a fresh node: nothing existing is moved
      a.click();
      a.remove();
      return;
    }
    var detail = (body && body.detail) || {};
    var key = CODE_KEYS[detail.code] || 'dl_unavailable';
    notify(t(key, { mb: detail.max_mb || opts.maxMb || '' }));
  }

  window.uallakMediaViewer = { open: open, close: closeViewer, download: download,
                               bigThumb: bigThumb };
})();
