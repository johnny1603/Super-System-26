/* The owner's bell on every admin page: frustrated-client alerts
   (core/frustration) + big TikTok uploads waiting for his approval
   (core/tiktok_uploads).

   ADMIN-ONLY: nothing is drawn until GET /api/admin/frustration-alerts
   answers with the admin session (401/403 or a network error → nothing).
   Only admin pages load this file; no client page ever does.

   The badge = OPEN incidents (one per client conversation, never one per
   message) + uploads awaiting approval. Click → a panel: first the approvals
   (client, file, size, "אישור" / "דחייה" → POST
   /api/admin/tiktok-uploads/{client}/{job}/approve|decline), then each
   incident: client, severity, reason, the last messages, the suggested
   action, and a "טופל" button (POST .../handled). The approval email's links
   (?tiktok_upload=<client>:<job>&decision=...) only OPEN the panel focused on
   that item — no link decides anything by itself. The panel is
   position:fixed, appended to <body>, clamped to the viewport; nothing is
   moved in the DOM after build. */
(function () {
  'use strict';
  if (window.FrustrationWidget) return;
  window.FrustrationWidget = {};

  const API = '/api/admin/frustration-alerts';
  const UPLOADS_API = '/api/admin/tiktok-uploads';
  const POLL_MS = 2 * 60 * 1000;
  const REASONS = {
    how_many_times: 'כמה פעמים', why_not: 'למה לא', already_said: 'כבר אמרתי', slow: 'תלונה על איטיות',
    human: 'ביקש בן אדם', leave: 'מאיים לעזוב / לבטל', profanity: 'קללות', broken: 'משהו לא עובד',
    punctuation: 'סימני קריאה/שאלה', shouting: 'אותיות גדולות', repeated_request: 'חזר על בקשה',
    agent_failed_twice: 'הסוכן נכשל פעמיים באותה בקשה', long_wait: 'המתנה ארוכה לתשובה',
  };

  const css = `
  .fw-bell { position:relative; display:inline-flex; align-items:center; justify-content:center; width:34px; height:30px;
    background:#1F1F1F; color:#F7F4EF; border:1px solid rgba(255,255,255,0.08); border-radius:100px; cursor:pointer;
    font-size:15px; margin-inline-start:8px; padding:0; }
  .fw-bell:hover { border-color:rgba(255,255,255,0.25); }
  .fw-badge { position:absolute; top:-5px; inset-inline-end:-5px; min-width:17px; height:17px; border-radius:9px;
    background:#FF3B3B; color:#fff; font-size:10.5px; font-weight:700; display:flex; align-items:center;
    justify-content:center; padding:0 4px; font-family:'Heebo',sans-serif; }
  .fw-badge[hidden] { display:none; }
  .fw-fixed { position:fixed; top:10px; left:300px; z-index:9998; }
  .fw-panel { position:fixed; z-index:9999; width:min(420px, calc(100vw - 32px)); background:#171717; color:#F7F4EF;
    border:1px solid rgba(255,255,255,0.12); border-radius:14px; padding:14px; font-size:13px; direction:rtl;
    font-family:'Heebo',sans-serif; box-shadow:0 12px 40px rgba(0,0,0,0.5); }
  .fw-panel[hidden] { display:none; }
  .fw-panel h3 { margin:0 0 10px; font-size:14px; }
  .fw-panel h3.fw-sub { margin-top:14px; }
  .fw-item { border:1px solid rgba(255,255,255,0.08); border-radius:10px; padding:10px; margin-bottom:8px; }
  .fw-item.high { border-color:rgba(255,59,59,0.55); }
  .fw-item.focus { border-color:#FF4C1F; box-shadow:0 0 0 2px rgba(255,76,31,0.35); }
  .fw-head { display:flex; justify-content:space-between; gap:8px; align-items:center; }
  .fw-sev { font-size:11px; border-radius:100px; padding:2px 8px; background:#FFB020; color:#0D0D0D; white-space:nowrap; }
  .fw-item.high .fw-sev { background:#FF3B3B; color:#fff; }
  .fw-meta { color:#8A8A85; font-size:11.5px; margin:4px 0; overflow-wrap:anywhere; }
  .fw-msgs { background:#0D0D0D; border-radius:8px; padding:6px 8px; margin:6px 0; font-size:12px; line-height:1.5;
    max-height:140px; overflow-y:auto; white-space:pre-wrap; overflow-wrap:anywhere; }
  .fw-msgs .c { color:#F7F4EF; } .fw-msgs .a { color:#8A8A85; }
  .fw-action { font-size:12.5px; margin:6px 0; }
  .fw-done { background:#FF4C1F; color:#fff; border:0; border-radius:8px; padding:6px 12px; cursor:pointer;
    font-family:inherit; font-size:12.5px; }
  .fw-no { background:#2A2A2A; color:#F7F4EF; border:1px solid rgba(255,255,255,0.15); border-radius:8px; padding:6px 12px;
    cursor:pointer; font-family:inherit; font-size:12.5px; margin-inline-start:6px; }
  .fw-empty { color:#8A8A85; }
  `;

  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  const when = iso => { if (!iso) return ''; try { return new Date(iso).toLocaleString('he-IL', { dateStyle: 'short', timeStyle: 'short' }); } catch (e) { return iso; } };
  const mb = bytes => { const n = Number(bytes) || 0; return n >= 1073741824 ? (n / 1073741824).toFixed(2) + 'GB' : Math.round(n / 1048576) + 'MB'; };

  async function call(method, path, base) {
    const r = await fetch((base || API) + (path || ''), { method, credentials: 'same-origin' });
    if (r.status === 401 || r.status === 403) { const e = new Error('auth'); e.auth = true; throw e; }
    let data = {}; try { data = await r.json(); } catch (e) { /* empty */ }
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return data.data;
  }

  // The email's link: which approval to focus (never acted on by itself)
  const focusRef = (() => {
    try { return new URLSearchParams(window.location.search).get('tiktok_upload') || ''; } catch (e) { return ''; }
  })();

  let root, bell, badge, panel, list;

  function build(anchor) {
    const style = el('style'); style.textContent = css; document.head.appendChild(style);
    root = el('span', anchor ? '' : 'fw-fixed');
    bell = el('button', 'fw-bell', '🔔'); bell.type = 'button'; bell.title = 'התראות';
    bell.setAttribute('aria-expanded', 'false');
    badge = el('span', 'fw-badge'); badge.hidden = true; bell.appendChild(badge);
    root.appendChild(bell);
    if (anchor && anchor.classList.contains('topbar-right')) anchor.insertBefore(root, anchor.firstChild);
    else if (anchor) anchor.appendChild(root);
    else document.body.appendChild(root);

    panel = el('div', 'fw-panel'); panel.hidden = true; panel.setAttribute('role', 'dialog');
    list = el('div'); panel.appendChild(list);
    document.body.appendChild(panel);

    bell.addEventListener('click', () => (panel.hidden ? open() : close()));
    document.addEventListener('click', ev => { if (!panel.hidden && !panel.contains(ev.target) && !root.contains(ev.target)) close(); });
    document.addEventListener('keydown', ev => { if (ev.key === 'Escape' && !panel.hidden) { close(); bell.focus(); } });
    window.addEventListener('resize', () => { if (!panel.hidden) place(); });
  }

  function place() {
    const r = bell.getBoundingClientRect();
    const w = panel.offsetWidth, vw = document.documentElement.clientWidth;
    panel.style.left = Math.max(16, Math.min(r.left - w / 2, vw - w - 16)) + 'px';
    panel.style.top = Math.min(r.bottom + 8, window.innerHeight - 40) + 'px';
    panel.style.maxHeight = (window.innerHeight - r.bottom - 24) + 'px';
    panel.style.overflowY = 'auto';
  }
  function open() { panel.hidden = false; bell.setAttribute('aria-expanded', 'true'); place(); load(); }
  function close() { panel.hidden = true; bell.setAttribute('aria-expanded', 'false'); }

  function approvalItem(j) {
    const ref = j.client_id + ':' + j.id;
    const box = el('div', 'fw-item' + (ref === focusRef ? ' focus' : ''));
    const head = el('div', 'fw-head');
    head.appendChild(el('b', '', (j.client_name || 'לקוח') + ' #' + j.client_id));
    head.appendChild(el('span', 'fw-sev', mb(j.size)));
    box.appendChild(head);
    box.appendChild(el('div', 'fw-meta', `שליחה לטיוטות בטיקטוק · "${j.file_name || '—'}" · ${when(j.created_at)}`));
    box.appendChild(el('div', 'fw-action', 'מעל סף ההעלאה האוטומטית. ללקוח נאמר רק שאנחנו מטפלים. אישור = ההעלאה רצה בלי תקרה נוספת.'));
    const yes = el('button', 'fw-done', 'אישור'); yes.type = 'button';
    const no = el('button', 'fw-no', 'דחייה'); no.type = 'button';
    const decide = async (btn, decision) => {
      yes.disabled = no.disabled = true;
      try {
        await call('POST', '/' + encodeURIComponent(j.client_id) + '/' + encodeURIComponent(j.id) + '/' + decision, UPLOADS_API);
        load();
      } catch (e) { yes.disabled = no.disabled = false; btn.textContent = 'לא נשמר — שוב'; }
    };
    yes.addEventListener('click', () => decide(yes, 'approve'));
    no.addEventListener('click', () => decide(no, 'decline'));
    box.appendChild(yes); box.appendChild(no);
    return box;
  }

  function item(a) {
    const box = el('div', 'fw-item' + (a.severity === 'high' ? ' high' : ''));
    const head = el('div', 'fw-head');
    head.appendChild(el('b', '', (a.client_name || 'לקוח') + ' #' + a.client_id));
    head.appendChild(el('span', 'fw-sev', a.severity === 'high' ? 'חומרה גבוהה' : 'חומרה בינונית'));
    box.appendChild(head);
    const reasons = (a.reasons || []).map(r => REASONS[r] || r).join(' · ');
    box.appendChild(el('div', 'fw-meta', `${reasons} · ${when(a.last_seen_at)} · ${a.message_count || 1} הודעות`));
    const msgs = el('div', 'fw-msgs');
    (a.messages || []).slice(-5).forEach(m => {
      const line = el('div', m.from === 'client' ? 'c' : 'a', (m.from === 'client' ? 'לקוח: ' : 'סוכן: ') + (m.text || ''));
      msgs.appendChild(line);
    });
    box.appendChild(msgs);
    if (a.suggested_action) box.appendChild(el('div', 'fw-action', 'מה כדאי לעשות: ' + a.suggested_action));
    if (a.status === 'open') {
      const done = el('button', 'fw-done', 'טופל'); done.type = 'button';
      done.addEventListener('click', async () => {
        done.disabled = true;
        try { await call('POST', '/' + encodeURIComponent(a.id) + '/handled'); load(); }
        catch (e) { done.disabled = false; done.textContent = 'לא נשמר — שוב'; }
      });
      box.appendChild(done);
    }
    return box;
  }

  function render(d, approvals) {
    const n = (d.open || 0) + approvals.length;
    badge.hidden = !n; badge.textContent = n > 99 ? '99+' : String(n);
    bell.title = n ? `${n} התראות פתוחות` : 'אין התראות פתוחות';
    list.textContent = '';
    if (approvals.length) {
      list.appendChild(el('h3', '', 'מחכים לאישור שלך'));
      approvals.forEach(j => list.appendChild(approvalItem(j)));
    }
    list.appendChild(el('h3', approvals.length ? 'fw-sub' : '', 'לקוחות מתוסכלים'));
    if (!d.available) { list.appendChild(el('div', 'fw-empty', 'הטבלה עוד לא קיימת — צריך להריץ את המיגרציה')); return; }
    if (!(d.alerts || []).length) { list.appendChild(el('div', 'fw-empty', 'אין התראות פתוחות')); return; }
    d.alerts.forEach(a => list.appendChild(item(a)));
  }

  let focusedOnce = false;
  async function load() {
    try {
      const d = await call('GET', '?status=open');
      let approvals = [];
      try { approvals = ((await call('GET', '?status=awaiting_admin', UPLOADS_API)) || {}).jobs || []; }
      catch (e) { approvals = []; }
      if (!root) build(document.querySelector('.topbar-right') || document.querySelector('.topbar'));
      render(d || {}, approvals);
      if (focusRef && !focusedOnce) { focusedOnce = true; if (panel.hidden) { panel.hidden = false; bell.setAttribute('aria-expanded', 'true'); place(); } }
    } catch (e) { /* not an admin session or unavailable: draw nothing new */ }
  }

  function start() {
    load();
    setInterval(() => { if (!document.hidden) load(); }, POLL_MS);
    window.addEventListener('focus', () => { if (!root) load(); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
