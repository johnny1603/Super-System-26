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
 * - Choice persists in localStorage ('uallak_lang') across all pages.
 */
(function () {
  'use strict';

  var SUPPORTED = ['he', 'en', 'fr', 'ar', 'ru'];
  var RTL = ['he', 'ar'];
  var NATIVE_NAMES = { he: 'עברית', en: 'English', fr: 'Français', ar: 'العربية', ru: 'Русский' };
  // The switcher's menu label is deliberately NOT the same as NATIVE_NAMES
  // for Arabic: "ערבית" (Hebrew) instead of "العربية" (Arabic script), so the
  // option reads unambiguously regardless of which language is currently
  // active on screen. Paired with FLAGS.ar below (see its own comment).
  var MENU_LABELS = { he: 'עברית', en: 'English', fr: 'Français', ar: 'ערבית', ru: 'Русский' };
  // Flag-style badge per language. Arabic deliberately uses Israel's flag,
  // NOT a generic Arabic-speaking-country flag (Saudi/Egypt/etc.) - these are
  // Israeli Arabic-speaking clients, not a foreign audience, and a foreign
  // flag would misrepresent who they are.
  var FLAGS = { he: '🇮🇱', en: '🇺🇸', fr: '🇫🇷', ar: '🇮🇱', ru: '🇷🇺' };
  var LOCALES = { he: 'he-IL', en: 'en-GB', fr: 'fr-FR', ar: 'ar', ru: 'ru-RU' };
  var STORAGE_KEY = 'uallak_lang';

  var table = {};
  var listeners = [];

  function storedLang() {
    try {
      var value = localStorage.getItem(STORAGE_KEY);
      return SUPPORTED.indexOf(value) !== -1 ? value : 'he';
    } catch (e) { return 'he'; }
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
    listeners.forEach(function (fn) { fn(lang); });
  }

  // Settings-style compact control: a round flag badge showing the CURRENT
  // language, opening a small dropdown menu (flag + label per option) on
  // click - replaces the old plain-text <select> everywhere mountSwitcher is
  // used (login, profile, dashboard, landing, terms), so the redesign only
  // needs to happen once, here, in the shared engine.
  var SWITCHER_STYLE_ID = 'uallak-i18n-switcher-style';

  function ensureSwitcherStyles() {
    if (document.getElementById(SWITCHER_STYLE_ID)) return;
    var style = document.createElement('style');
    style.id = SWITCHER_STYLE_ID;
    style.textContent =
      '.uallak-lang-switcher{position:relative;display:inline-block;line-height:0;}' +
      '.uallak-lang-badge{width:34px;height:34px;border-radius:50%;background:var(--accent,#FF4C1F);' +
      'color:#fff;border:none;font-size:16px;display:flex;align-items:center;justify-content:center;' +
      'cursor:pointer;padding:0;box-shadow:0 2px 8px rgba(0,0,0,0.22);transition:transform .15s;}' +
      '.uallak-lang-badge:hover{transform:scale(1.07);}' +
      '.uallak-lang-badge:focus-visible{outline:2px solid var(--accent,#FF4C1F);outline-offset:2px;}' +
      '.uallak-lang-menu{position:absolute;top:calc(100% + 8px);inset-inline-end:0;background:#fff;' +
      'border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,0.28);padding:6px;min-width:150px;' +
      'display:none;z-index:9999;}' +
      '.uallak-lang-menu.open{display:block;}' +
      '.uallak-lang-item{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:8px;' +
      'cursor:pointer;font-family:inherit;font-size:13.5px;color:#1A1A1A;white-space:nowrap;}' +
      '.uallak-lang-item:hover{background:rgba(0,0,0,0.06);}' +
      '.uallak-lang-item.active{background:rgba(255,76,31,0.12);font-weight:700;color:#FF4C1F;}' +
      '.uallak-lang-item .uallak-flag{font-size:16px;line-height:1;}';
    document.head.appendChild(style);
  }

  function mountSwitcher(container) {
    ensureSwitcherStyles();
    container.innerHTML = ''; // safe to re-mount

    var wrap = document.createElement('div');
    wrap.className = 'uallak-lang-switcher';

    var badge = document.createElement('button');
    badge.type = 'button';
    badge.className = 'uallak-lang-badge';
    badge.setAttribute('aria-label', 'Language / שפה');
    badge.setAttribute('aria-haspopup', 'true');
    badge.textContent = FLAGS[lang] || '🌐';

    var menu = document.createElement('div');
    menu.className = 'uallak-lang-menu';

    function renderMenu() {
      menu.innerHTML = '';
      SUPPORTED.forEach(function (code) {
        var item = document.createElement('div');
        item.className = 'uallak-lang-item' + (code === lang ? ' active' : '');
        item.setAttribute('role', 'button');
        var flagSpan = document.createElement('span');
        flagSpan.className = 'uallak-flag';
        flagSpan.textContent = FLAGS[code] || '🌐';
        var labelSpan = document.createElement('span');
        labelSpan.textContent = MENU_LABELS[code];
        item.appendChild(flagSpan);
        item.appendChild(labelSpan);
        item.addEventListener('click', function (e) {
          e.stopPropagation();
          setLanguage(code);
          menu.classList.remove('open');
        });
        menu.appendChild(item);
      });
    }
    renderMenu();

    badge.addEventListener('click', function (e) {
      e.stopPropagation();
      menu.classList.toggle('open');
    });
    document.addEventListener('click', function () { menu.classList.remove('open'); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') menu.classList.remove('open');
    });

    // Keep the badge + menu in sync if the language changes (this switch, or
    // in principle another one on the same page)
    listeners.push(function (newLang) {
      badge.textContent = FLAGS[newLang] || '🌐';
      renderMenu();
    });

    wrap.appendChild(badge);
    wrap.appendChild(menu);
    container.appendChild(wrap);
    return wrap;
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
