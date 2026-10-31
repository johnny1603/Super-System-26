/* uallak i18n engine — shared by every client-facing page.
 *
 * Pattern (see .claude/skills/i18n/SKILL.md):
 * - This file is the ENGINE only. Each page keeps its own string table
 *   (locality: strings live next to the markup that uses them) and calls
 *   uallakI18n.init(TABLE) once after the DOM exists.
 * - Static elements carry data-i18n="key" (textContent), data-i18n-placeholder,
 *   or data-i18n-title; JS-generated strings call uallakI18n.t('key', {vars}).
 * - Table shape: { key: { he: '...', en: '...', fr: '...', ar: '...', ru: '...' } }
 *   {name}-style placeholders are interpolated by t().
 * - Hebrew is the source language and the fallback for any missing key, so a
 *   partially translated page degrades to Hebrew, never to raw keys.
 * - Direction: he/ar are RTL, en/fr/ru are LTR — setLanguage() flips
 *   <html dir> and <html lang>, which cascades to the whole layout.
 * - Choice persists in localStorage ('uallak_lang') across all pages. With
 *   nothing stored yet, the browser's own language picks the starting one
 *   (English if it asks only for languages we don't carry).
 */
(function () {
  'use strict';

  var SUPPORTED = ['he', 'en', 'fr', 'ar', 'ru'];
  var RTL = ['he', 'ar'];
  // Every switcher button's aria-label + title: there is no visible text, so
  // this is the only way a screen reader or a mouse hover learns the language
  var NATIVE_NAMES = { he: 'עברית', en: 'English', fr: 'Français', ar: 'العربية', ru: 'Русский' };
  var LOCALES = { he: 'he-IL', en: 'en-GB', fr: 'fr-FR', ar: 'ar', ru: 'ru-RU' };
  var STORAGE_KEY = 'uallak_lang';

  // Flags are inline SVG, NEVER emoji (2026-10-31). The previous switcher drew
  // regional-indicator flag emoji (IL/US/FR/RU), and Windows ships no flag glyphs:
  // every button rendered as two grey letters ("IL", "US", ...). SVG renders
  // the same on every OS. Arabic uses the UAE colours. Drawn on a 40x40 box,
  // clipped to a circle, with a highlight + rim shade for depth.
  var FLAG_BODY = {
    he: '<rect width="40" height="40" fill="#fff"/><rect y="7" width="40" height="5" fill="#0038B8"/>' +
        '<rect y="28" width="40" height="5" fill="#0038B8"/><path d="M20 14.2l4.3 7.4h-8.6zM20 25.8l-4.3-7.4h8.6" ' +
        'fill="none" stroke="#0038B8" stroke-width="1.5"/>',
    en: '<rect width="40" height="40" fill="#012169"/><path d="M0 0l40 40M40 0L0 40" stroke="#fff" stroke-width="8"/>' +
        '<path d="M0 0l40 40M40 0L0 40" stroke="#C8102E" stroke-width="4"/><path d="M20 0v40M0 20h40" stroke="#fff" ' +
        'stroke-width="13"/><path d="M20 0v40M0 20h40" stroke="#C8102E" stroke-width="7"/>',
    fr: '<rect x="0" width="13.34" height="40" fill="#002395"/><rect x="13.34" width="13.33" height="40" fill="#fff"/>' +
        '<rect x="26.67" width="13.33" height="40" fill="#ED2939"/>',
    ar: '<rect x="11" y="0" width="29" height="13.34" fill="#009A3E"/><rect x="11" y="13.34" width="29" height="13.33" ' +
        'fill="#fff"/><rect x="11" y="26.67" width="29" height="13.33" fill="#000"/><rect x="0" y="0" width="11" ' +
        'height="40" fill="#CE1126"/>',
    ru: '<rect y="0" width="40" height="13.34" fill="#fff"/><rect y="13.34" width="40" height="13.33" fill="#0039A6"/>' +
        '<rect y="26.67" width="40" height="13.33" fill="#D52B1E"/>',
  };
  var flagSeq = 0;

  // Each SVG carries its OWN defs under ids unique to this button, so no flag
  // depends on another one existing (or being visible) on the page
  function flagSvg(code) {
    var p = 'uallak-flag-' + (++flagSeq) + '-';
    return '<svg viewBox="0 0 40 40" aria-hidden="true" focusable="false"><defs>' +
      '<clipPath id="' + p + 'c"><circle cx="20" cy="20" r="20"/></clipPath>' +
      '<radialGradient id="' + p + 's" cx="33%" cy="28%" r="78%">' +
      '<stop offset="0%" stop-color="#fff" stop-opacity=".55"/><stop offset="42%" stop-color="#fff" stop-opacity=".10"/>' +
      '<stop offset="72%" stop-color="#000" stop-opacity=".10"/><stop offset="100%" stop-color="#000" stop-opacity=".48"/>' +
      '</radialGradient><radialGradient id="' + p + 'r" cx="50%" cy="50%" r="50%">' +
      '<stop offset="88%" stop-color="#000" stop-opacity="0"/><stop offset="100%" stop-color="#000" stop-opacity=".40"/>' +
      '</radialGradient></defs><g clip-path="url(#' + p + 'c)">' + (FLAG_BODY[code] || '') +
      '<rect width="40" height="40" fill="url(#' + p + 's)"/><rect width="40" height="40" fill="url(#' + p + 'r)"/></g></svg>';
  }

  var table = {};
  var listeners = [];

  // First visit only (nothing stored yet): follow the browser's own language
  // list, so a non-Hebrew visitor lands on a page they can actually read
  // instead of always getting Hebrew. A browser that asks only for languages
  // we don't carry (German, Chinese, ...) gets ENGLISH — the one a foreign
  // visitor or a platform reviewer is likeliest to read. Hebrew stays the
  // fallback when the browser reports nothing at all, and per missing KEY (t()).
  function browserLang() {
    try {
      var candidates = navigator.languages || [navigator.language];
      var asked = false;
      for (var i = 0; i < candidates.length; i++) {
        if (!candidates[i]) continue;
        asked = true;
        var base = String(candidates[i]).toLowerCase().split('-')[0];
        if (base === 'iw') base = 'he'; // legacy Hebrew tag, still sent by some browsers
        if (SUPPORTED.indexOf(base) !== -1) return base;
      }
      if (asked) return 'en';
    } catch (e) { /* no navigator (SSR, odd embed) — fall through */ }
    return 'he';
  }

  function storedLang() {
    try {
      var value = localStorage.getItem(STORAGE_KEY);
      if (SUPPORTED.indexOf(value) !== -1) return value; // explicit choice always wins
    } catch (e) { /* private mode — treat as "nothing stored" */ }
    return browserLang();
  }

  var lang = storedLang();

  function t(key, vars) {
    var entry = table[key] || {};
    var text = entry[lang] != null ? entry[lang] : entry.he;
    if (text == null) return key; // visible marker for a missing key
    if (vars) {
      Object.keys(vars).forEach(function (name) {
        text = text.split('{' + name + '}').join(String(vars[name]));
      });
    }
    return text;
  }

  function applyDom() {
    document.documentElement.lang = lang;
    document.documentElement.dir = RTL.indexOf(lang) !== -1 ? 'rtl' : 'ltr';
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      el.textContent = t(el.getAttribute('data-i18n'));
    });
    // For strings that carry OUR OWN inline markup (<b>, <br>) - never used
    // with user-provided content
    document.querySelectorAll('[data-i18n-html]').forEach(function (el) {
      el.innerHTML = t(el.getAttribute('data-i18n-html'));
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
      el.setAttribute('placeholder', t(el.getAttribute('data-i18n-placeholder')));
    });
    document.querySelectorAll('[data-i18n-title]').forEach(function (el) {
      el.setAttribute('title', t(el.getAttribute('data-i18n-title')));
    });
    if (table._page_title) document.title = t('_page_title');
  }

  function setLanguage(next) {
    if (SUPPORTED.indexOf(next) === -1) return;
    lang = next;
    try { localStorage.setItem(STORAGE_KEY, next); } catch (e) { /* private mode */ }
    applyDom();
    // One page re-render throwing must not stop the rest (or the switcher's
    // own ring) from updating — every listener runs, each in isolation
    listeners.forEach(function (fn) {
      try { fn(lang); } catch (e) { if (window.console) console.error('i18n listener failed', e); }
    });
  }

  // The switcher (from the owner's flag design file, 2026-10-31): one row
  // of round flag buttons with depth, no visible text, the active one ringed
  // in the brand accent. All five are always on screen — one tap switches,
  // there is no popover to open first (the old popover collapsed into a
  // one-column stack inside the narrow top bar). Used by every page that
  // calls mountSwitcher (login, profile, dashboard, landing, terms, sales chat).
  var SWITCHER_STYLE_ID = 'uallak-i18n-switcher-style';

  function ensureSwitcherStyles() {
    if (document.getElementById(SWITCHER_STYLE_ID)) return;
    var style = document.createElement('style');
    style.id = SWITCHER_STYLE_ID;
    style.textContent =
      '.uallak-lang-row{display:inline-flex;gap:10px;align-items:center;flex-wrap:nowrap;vertical-align:middle;}' +
      '.lang-btn{width:34px;height:34px;padding:0;border:0;border-radius:50%;flex:0 0 auto;' +
      'background:transparent;cursor:pointer;line-height:0;-webkit-tap-highlight-color:transparent;' +
      'touch-action:manipulation;filter:drop-shadow(0 3px 5px rgba(0,0,0,.55));' +
      'transition:transform .16s ease,filter .16s ease;}' +
      '@media (hover:hover){.lang-btn:hover{transform:translateY(-2px) scale(1.06);' +
      'filter:drop-shadow(0 6px 9px rgba(0,0,0,.6));}}' +
      '.lang-btn:active{transform:translateY(0) scale(.97);}' +
      '.lang-btn svg{width:100%;height:100%;display:block;border-radius:50%;pointer-events:none;}' +
      // The active one: a ring in the brand accent, separated by a gap in the page background
      '.lang-btn[aria-current="true"]{box-shadow:0 0 0 2px var(--bg,#0D0D0D),0 0 0 4px var(--accent,#FF4C1F);' +
      'transform:scale(1.04);}' +
      '.lang-btn:focus-visible{outline:2px solid var(--accent,#FF4C1F);outline-offset:4px;}' +
      // Phones: smaller, tighter — still a 28px+ target, the row never forces a horizontal scroll
      '@media (max-width:640px){.uallak-lang-row{gap:8px;}.lang-btn{width:28px;height:28px;}}';
    document.head.appendChild(style);
  }

  function paintActive(row) {
    row.querySelectorAll('.lang-btn').forEach(function (btn) {
      var on = btn.getAttribute('data-lang') === lang;
      if (on) btn.setAttribute('aria-current', 'true'); else btn.removeAttribute('aria-current');
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  function mountSwitcher(container) {
    if (!container) return null;
    ensureSwitcherStyles();
    container.innerHTML = ''; // safe to re-mount

    var row = document.createElement('div');
    row.className = 'uallak-lang-row';
    row.setAttribute('role', 'group');
    row.setAttribute('aria-label', 'Language / שפה');

    SUPPORTED.forEach(function (code) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'lang-btn';
      btn.setAttribute('data-lang', code);
      // No visible text — the name lives in aria-label AND title, always
      btn.setAttribute('aria-label', NATIVE_NAMES[code]);
      btn.setAttribute('title', NATIVE_NAMES[code]);
      btn.setAttribute('lang', code);
      btn.innerHTML = flagSvg(code);
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        if (code !== lang) setLanguage(code);
      });
      row.appendChild(btn);
    });
    paintActive(row);

    // Repaint the ring whenever the language changes (this row or any other)
    listeners.push(function () { paintActive(row); });

    container.appendChild(row);
    return row;
  }

  // Server error-code pattern: API failures return detail={"code": "ERR_X"}
  // instead of raw Hebrew prose (see .claude/skills/i18n/SKILL.md) - this
  // resolves a fetch response's parsed body to a translated string, looking
  // for the lowercased code (e.g. "ERR_NOT_CONNECTED" -> key
  // "err_not_connected") in the PAGE's own table first, and falling back to
  // a generic key when the code is missing or unmapped. Never surfaces
  // body.detail itself, so a server string can't leak past the current UI
  // language by accident.
  function errorText(body, fallbackKey) {
    var code = body && body.detail && body.detail.code;
    if (code) {
      var key = String(code).toLowerCase();
      if (table[key]) return t(key);
    }
    return t(fallbackKey);
  }

  window.uallakI18n = {
    t: t,
    init: function (pageTable) { table = pageTable || {}; applyDom(); },
    setLanguage: setLanguage,
    current: function () { return lang; },
    locale: function () { return LOCALES[lang] || 'he-IL'; },
    isRtl: function () { return RTL.indexOf(lang) !== -1; },
    onChange: function (fn) { listeners.push(fn); },
    mountSwitcher: mountSwitcher,
    errorText: errorText,
    SUPPORTED: SUPPORTED,
  };
})();
