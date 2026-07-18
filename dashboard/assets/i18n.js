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

  function mountSwitcher(container) {
    var select = document.createElement('select');
    select.className = 'lang-switcher';
    select.setAttribute('aria-label', 'Language');
    SUPPORTED.forEach(function (code) {
      var option = document.createElement('option');
      option.value = code;
      option.textContent = NATIVE_NAMES[code];
      if (code === lang) option.selected = true;
      select.appendChild(option);
    });
    select.addEventListener('change', function () { setLanguage(select.value); });
    container.appendChild(select);
    return select;
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
    SUPPORTED: SUPPORTED,
  };
})();
