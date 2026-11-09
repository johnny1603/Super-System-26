/* Claude credits — the owner's header indicator on every admin page
   (core/anthropic_credits, docs/ANTHROPIC-CREDITS.md).

   ADMIN-ONLY: nothing is drawn until GET /api/admin/credits answers with the
   admin session (401/403 or a network error → nothing at all). Only admin
   pages load this file; no client page ever does.

   Shows: a status dot (green / yellow / red), the estimated remaining USD
   and days left, and a permanent "Top up" link to the Anthropic Console
   billing page (new tab, noopener noreferrer). Click the indicator for the
   panel: measured spend since the last balance, last 24 h / 7 d, average
   daily burn, the "balance after top-up" field, the thresholds, the note.

   The panel is position:fixed and appended to <body> (a transformed header
   would re-anchor it), clamped to the viewport. Nothing is ever moved in
   the DOM after it is built (the CLAUDE.md re-parenting trap). */
(function () {
  'use strict';
  if (window.CreditsWidget) return;
  window.CreditsWidget = {};

  const API = '/api/admin/credits';
  const CONSOLE = 'https://console.anthropic.com/settings/billing/';
  const POLL_MS = 10 * 60 * 1000;
  const STATUS = { green: ['#00C96E', 'תקין'], yellow: ['#FFB020', 'כדאי להטעין בקרוב'],
                   red: ['#FF3B3B', 'להטעין עכשיו'], unknown: ['#8A8A85', 'אין יתרה רשומה'] };

  const css = `
  .cw-credits { display:inline-flex; align-items:center; gap:8px; font-family:'Heebo',sans-serif; direction:rtl; }
  .cw-credits-pill { display:inline-flex; align-items:center; gap:7px; background:#1F1F1F; color:#F7F4EF;
    border:1px solid rgba(255,255,255,0.08); border-radius:100px; padding:5px 12px; font-size:12px; cursor:pointer;
    font-family:inherit; max-width:52vw; }
  .cw-credits-pill:hover { border-color:rgba(255,255,255,0.25); }
  .cw-credits-pill .t { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; display:block; }
  .cw-credits-dot { width:9px; height:9px; border-radius:50%; flex-shrink:0; }
  .cw-credits-top { background:#FF4C1F; color:#fff; border-radius:100px; padding:5px 12px; font-size:12px;
    font-weight:600; text-decoration:none; white-space:nowrap; }
  .cw-credits-top:hover { filter:brightness(1.1); }
  .cw-credits-fixed { position:fixed; top:10px; left:12px; z-index:9998; }
  .cw-credits-panel { position:fixed; z-index:9999; width:min(360px, calc(100vw - 32px)); background:#171717;
    color:#F7F4EF; border:1px solid rgba(255,255,255,0.12); border-radius:14px; padding:16px; font-size:13px;
    box-shadow:0 12px 40px rgba(0,0,0,0.5); direction:rtl; font-family:'Heebo',sans-serif; }
  .cw-credits-panel[hidden] { display:none; }
  .cw-credits-panel h3 { font-size:14px; margin:0 0 10px; display:flex; align-items:center; gap:8px; }
  .cw-credits-row { display:flex; justify-content:space-between; gap:10px; padding:4px 0;
    border-bottom:1px solid rgba(255,255,255,0.05); }
  .cw-credits-row b { font-weight:600; direction:ltr; unicode-bidi:isolate; }
  .cw-credits-form { display:flex; gap:6px; margin-top:12px; flex-wrap:wrap; }
  .cw-credits-form input { flex:1 1 90px; min-width:0; background:#0D0D0D; color:#F7F4EF;
    border:1px solid rgba(255,255,255,0.15); border-radius:8px; padding:7px 9px; font-family:inherit; font-size:13px; }
  .cw-credits-form button { background:#FF4C1F; color:#fff; border:0; border-radius:8px; padding:7px 12px;
    font-family:inherit; font-size:13px; cursor:pointer; }
  .cw-credits-form button.sec { background:#1F1F1F; border:1px solid rgba(255,255,255,0.15); }
  .cw-credits-msg { min-height:16px; font-size:12px; margin-top:6px; color:#FFB020; }
  .cw-credits-note { color:#8A8A85; font-size:11.5px; margin-top:10px; line-height:1.5; }
  .cw-credits-label { color:#8A8A85; font-size:11.5px; margin-top:12px; }
  `;

  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  const usd = v => (v == null ? '—' : '$' + Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const when = iso => { if (!iso) return '—'; try { return new Date(iso).toLocaleString('he-IL', { dateStyle: 'short', timeStyle: 'short' }); } catch (e) { return iso; } };

  async function call(method, path, body) {
    const r = await fetch(API + path, { method, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
    if (r.status === 401 || r.status === 403) { const e = new Error('auth'); e.auth = true; throw e; }
    let data = {}; try { data = await r.json(); } catch (e) { /* empty */ }
    if (!r.ok) { const d = data.detail || {}; const e = new Error(d.code || ('HTTP ' + r.status)); e.detail = d.detail || ''; throw e; }
    return data.data;
  }

  const ERR = { ERR_BAD_AMOUNT: 'סכום לא תקין', ERR_BAD_THRESHOLDS: 'צריך: אדום קטן מצהוב, עד 90 ימים',
                ERR_MIGRATION_PENDING: 'הטבלה עוד לא קיימת — צריך להריץ את המיגרציה',
                ERR_AUDIT_UNAVAILABLE: 'יומן הפעולות לא זמין — לא נשמר', ERR_CREDITS_UNAVAILABLE: 'לא הצלחתי לחשב עכשיו' };

  let root, pill, dot, text, panel, rows, msg, balanceInput, noteInput, yellowInput, redInput, histEl, titleDot;

  function build(anchor) {
    const style = el('style'); style.textContent = css; document.head.appendChild(style);
    root = el('div', 'cw-credits' + (anchor ? '' : ' cw-credits-fixed'));
    pill = el('button', 'cw-credits-pill'); pill.type = 'button'; pill.setAttribute('aria-expanded', 'false');
    pill.title = 'קרדיט Claude (הערכה)';
    dot = el('span', 'cw-credits-dot'); text = el('span', 't', 'קרדיט…');
    pill.appendChild(dot); pill.appendChild(text);
    const top = el('a', 'cw-credits-top', 'הטענת קרדיט');
    top.href = CONSOLE; top.target = '_blank'; top.rel = 'noopener noreferrer';
    root.appendChild(pill); root.appendChild(top);
    if (anchor && anchor.classList.contains('topbar-right')) anchor.insertBefore(root, anchor.firstChild);
    else if (anchor) anchor.appendChild(root);
    else document.body.appendChild(root);

    panel = el('div', 'cw-credits-panel'); panel.hidden = true; panel.setAttribute('role', 'dialog');
    const h = el('h3'); titleDot = el('span', 'cw-credits-dot'); h.appendChild(titleDot); h.appendChild(el('span', '', 'קרדיט Claude — הערכה'));
    panel.appendChild(h);
    rows = el('div'); panel.appendChild(rows);

    panel.appendChild(el('div', 'cw-credits-label', 'יתרה אחרי הטענה (USD) — השעה נרשמת אוטומטית'));
    const f1 = el('form', 'cw-credits-form');
    balanceInput = el('input'); balanceInput.type = 'number'; balanceInput.min = '0'; balanceInput.step = '0.01';
    balanceInput.placeholder = 'למשל 250'; balanceInput.inputMode = 'decimal'; balanceInput.required = true;
    noteInput = el('input'); noteInput.type = 'text'; noteInput.maxLength = 200; noteInput.placeholder = 'הערה (לא חובה)';
    const save = el('button', '', 'שמירה'); save.type = 'submit';
    f1.appendChild(balanceInput); f1.appendChild(noteInput); f1.appendChild(save);
    f1.addEventListener('submit', async ev => {
      ev.preventDefault(); msg.textContent = '';
      try {
        render(await call('POST', '/balance', { balance_usd: Number(balanceInput.value), note: noteInput.value }));
        balanceInput.value = ''; noteInput.value = ''; msg.textContent = 'נשמר ✓';
      } catch (e) { msg.textContent = ERR[e.message] || e.message; }
    });
    panel.appendChild(f1);

    panel.appendChild(el('div', 'cw-credits-label', 'סף צהוב / אדום (ימים שנותרו)'));
    const f2 = el('form', 'cw-credits-form');
    yellowInput = el('input'); yellowInput.type = 'number'; yellowInput.min = '0.5'; yellowInput.step = '0.5'; yellowInput.title = 'צהוב מתחת ל-';
    redInput = el('input'); redInput.type = 'number'; redInput.min = '0.5'; redInput.step = '0.5'; redInput.title = 'אדום מתחת ל-';
    const saveT = el('button', 'sec', 'עדכון ספים'); saveT.type = 'submit';
    f2.appendChild(yellowInput); f2.appendChild(redInput); f2.appendChild(saveT);
    f2.addEventListener('submit', async ev => {
      ev.preventDefault(); msg.textContent = '';
      try {
        render(await call('PUT', '/thresholds', { yellow_days: Number(yellowInput.value), red_days: Number(redInput.value) }));
        msg.textContent = 'הספים עודכנו ✓';
      } catch (e) { msg.textContent = ERR[e.message] || e.message; }
    });
    panel.appendChild(f2);
    msg = el('div', 'cw-credits-msg'); panel.appendChild(msg);
    histEl = el('div', 'cw-credits-note'); panel.appendChild(histEl);
    panel.appendChild(el('div', 'cw-credits-note',
      'הערכה לפי המדידה שלנו; הקונסולה של Anthropic היא מקור האמת. מומלץ להפעיל שם Auto reload.'));
    document.body.appendChild(panel);

    pill.addEventListener('click', () => (panel.hidden ? open() : close()));
    document.addEventListener('click', ev => { if (!panel.hidden && !panel.contains(ev.target) && !root.contains(ev.target)) close(); });
    document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && !panel.hidden) { close(); pill.focus(); } });
    window.addEventListener('resize', () => { if (!panel.hidden) place(); });
  }

  function place() {
    const r = pill.getBoundingClientRect();
    const w = panel.offsetWidth, vw = document.documentElement.clientWidth;
    let left = Math.min(Math.max(16, r.left), vw - w - 16);
    panel.style.left = Math.max(16, left) + 'px';
    panel.style.top = Math.min(r.bottom + 8, window.innerHeight - 40) + 'px';
    panel.style.maxHeight = (window.innerHeight - r.bottom - 24) + 'px';
    panel.style.overflowY = 'auto';
  }
  function open() { panel.hidden = false; pill.setAttribute('aria-expanded', 'true'); place(); load(); }
  function close() { panel.hidden = true; pill.setAttribute('aria-expanded', 'false'); }

  function row(label, value) { const d = el('div', 'cw-credits-row'); d.appendChild(el('span', '', label)); d.appendChild(el('b', '', value)); return d; }

  function render(d) {
    const [color, word] = STATUS[d.status] || STATUS.unknown;
    dot.style.background = color; titleDot.style.background = color;
    const days = d.days_left == null ? '' : ` · ${d.days_left} ימים`;
    text.textContent = d.balance ? `${usd(d.remaining_usd)}${days}` : 'קרדיט: אין יתרה רשומה';
    pill.title = `קרדיט Claude — ${word} (הערכה)`;
    rows.textContent = '';
    rows.appendChild(row('מצב', word));
    if (d.migration_pending) rows.appendChild(row('טבלה', 'המיגרציה עוד לא הורצה'));
    rows.appendChild(row('יתרה אחרונה שהוזנה', d.balance ? `${usd(d.balance.usd)} · ${when(d.balance.entered_at)}` : '—'));
    rows.appendChild(row('הוצאה מאז (מדידה)', usd(d.spent_since_balance_usd)));
    rows.appendChild(row('נשאר בהערכה', usd(d.remaining_usd)));
    rows.appendChild(row('24 שעות אחרונות', usd(d.spend_24h_usd)));
    rows.appendChild(row('7 ימים אחרונים', usd(d.spend_7d_usd)));
    rows.appendChild(row('ממוצע יומי', usd(d.daily_burn_usd)));
    rows.appendChild(row('ימים שנותרו', d.days_left == null ? '—' : String(d.days_left)));
    if (d.truncated) rows.appendChild(row('שימו לב', 'יותר מדי שורות — ההערכה חלקית'));
    const t = d.thresholds || {};
    if (document.activeElement !== yellowInput) yellowInput.value = t.yellow_days != null ? t.yellow_days : 7;
    if (document.activeElement !== redInput) redInput.value = t.red_days != null ? t.red_days : 2;
    const hist = (d.history || []).slice(0, 5).map(h => `${usd(h.balance_usd)} · ${when(h.entered_at)}`);
    histEl.textContent = hist.length ? 'הזנות אחרונות: ' + hist.join(' | ') : '';
  }

  async function load(first) {
    try {
      const d = await call('GET', '');
      if (!root) build(document.querySelector('.topbar-right') || document.querySelector('.topbar'));
      render(d);
    } catch (e) {
      if (e.auth) return;                       // not an admin session: draw nothing
      if (first && !root && e.message !== 'auth' && /^ERR_|^HTTP 5/.test(e.message)) {
        // The admin is signed in but the estimate failed: the Top-up link still shows
        build(document.querySelector('.topbar-right') || document.querySelector('.topbar'));
        text.textContent = 'קרדיט: לא זמין'; dot.style.background = STATUS.unknown[0];
      } else if (root) { msg.textContent = ERR[e.message] || ''; }
    }
  }

  function start() {
    load(true);
    setInterval(() => { if (!document.hidden) load(); }, POLL_MS);
    // Signed in after the page loaded (the login overlay): appear on return to the tab
    window.addEventListener('focus', () => { if (!root) load(true); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
