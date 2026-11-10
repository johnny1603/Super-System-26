/* איציק — the owner's operator as a floating chat on every admin page
   (core/operator_agent, docs/OPERATOR.md).

   Self-contained: one <script src="/assets/itzik-chat.js" defer>, its own CSS,
   its own DOM under one root, no page globals except window.ItzikChat. The
   mobile app reuses it as is.

   ADMIN-ONLY: nothing is drawn until GET /api/admin/operator/presence answers
   with the admin session. Without it (a client, a logged-out admin, any
   error) the page gets nothing at all. Only admin pages load this file.

   The look follows the client agent chat (dashboard/client/index.html):
   minimised = the standing figure waiting in the corner; open = the window
   with the portrait in the header. Minimise/restore is the .minimized class
   ONLY — no node is ever moved (the CLAUDE.md re-parenting trap).

   PRESENCE: a badge on the figure for new alerts / a pending approval / a
   post-deploy problem (core/operator_presence). When the new deploy looks
   wrong, ONE proactive line with the one-tap rollback — at most once per
   browser session per signature (sessionStorage), so it returns only when
   something new happens.

   SECURITY: unchanged. Tier 2 buttons call the same audited routes; a Tier 3
   approval still needs the emailed one-time code, checked server-side. */
(function () {
  'use strict';
  if (window.ItzikChat) return;

  const API = '/api/admin/operator';
  const FACE = '/assets/agents/itzik-';
  const NAME = 'איציק';
  const WORKSPACE = '/admin/operator/';
  const SEEN_KEY = 'itzik_seen_at';          // localStorage: alerts older than this were seen
  const PROACTIVE_KEY = 'itzik_proactive';   // sessionStorage: proactive signature already shown
  const POLL_MS = 90000;

  // ─── Storage that never throws (private windows, blocked storage) ──────────
  function getStore(kind, key) { try { return window[kind].getItem(key); } catch (e) { return null; } }
  function setStore(kind, key, value) { try { window[kind].setItem(key, value); } catch (e) { /* fine */ } }

  // ─── API ───────────────────────────────────────────────────────────────────
  async function call(path, opts) {
    const r = await fetch(API + path, Object.assign({ credentials: 'same-origin', headers: { 'Content-Type': 'application/json' } }, opts || {}));
    if (r.status === 401 || r.status === 403) { const e = new Error('auth'); e.auth = true; throw e; }
    let body = {}; try { body = await r.json(); } catch (e) { /* empty */ }
    if (!r.ok) { const d = body.detail || {}; throw new Error((d.code || ('HTTP ' + r.status)) + (d.detail ? ' — ' + d.detail : '')); }
    return body.data;
  }
  const post = (path, data) => call(path, { method: 'POST', body: JSON.stringify(data || {}) });

  const ERR_HE = {
    ERR_GATE_WRONG: 'קוד שגוי', ERR_GATE_EXPIRED: 'הקוד פג תוקף או כבר נוצל — בקש קוד חדש', ERR_GATE_LOCKED: 'יותר מדי ניסיונות — בקש קוד חדש',
    ERR_GATE_MISMATCH: 'הקוד לא שייך לפעולה הזו או לחלון הזה', ERR_GATE_DISABLED: 'שינויי קוד כבויים (חסרות הגדרות)',
    ERR_TESTS_FAILED: 'הבדיקות נכשלו ב-PR — לא ממזג', ERR_TESTS_PENDING: 'הבדיקות עוד רצות — נסה שוב בעוד דקה',
    ERR_PR_CHANGED: 'מישהו דחף ל-PR אחרי שביקשת אישור — בקש קוד חדש', ERR_MASTER_MOVED: 'master השתנה מתחת לשינוי — בקש מאיציק להציע מחדש',
    ERR_OWNER_TAP_ONLY: 'רק בלחיצה שלך', ERR_NO_PREVIOUS_REVISION: 'אין גרסה קודמת מוכנה', ERR_FLAG_OWNER_ONLY: 'את זה אפשר רק לכבות מכאן',
    ERR_MESSAGE_TRUNCATED: 'ההודעה נשמרה מקוצרת (לפני 1.11) — לא שולח שוב', ERR_AUDIT_UNAVAILABLE: 'יומן הפעולות לא זמין — לא בוצע כלום'
  };
  function errText(e) { if (e && e.auth) return '⚠ לא מחובר לניהול'; const code = String(e.message || e).split(' — ')[0]; return '⚠ ' + (ERR_HE[code] || e.message || e); }

  // ─── DOM helpers ───────────────────────────────────────────────────────────
  function el(tag, cls, text) { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function when(iso) { if (!iso) return '—'; try { return new Date(iso).toLocaleString('he-IL', { dateStyle: 'short', timeStyle: 'short' }); } catch (e) { return iso; } }
  function changed() { try { window.dispatchEvent(new CustomEvent('itzik:changed')); } catch (e) { /* old browser */ } }

  // A face: WebP first, then the PNG, then the initial — like the client chat.
  function picture(kind, cls) {
    const holder = el('span', cls); holder.dataset.initial = 'א';
    const pic = document.createElement('picture');
    const src = document.createElement('source'); src.type = 'image/webp'; src.srcset = FACE + kind + '.webp';
    const img = document.createElement('img'); img.alt = ''; img.decoding = 'async'; img.src = FACE + kind + '.png';
    img.addEventListener('error', () => {
      if (src.parentNode && !img.dataset.retried) { img.dataset.retried = '1'; src.remove(); img.src = FACE + kind + '.png'; return; }
      holder.classList.add('failed');
    });
    pic.appendChild(src); pic.appendChild(img); holder.appendChild(pic);
    return holder;
  }

  // ─── Actions and cards (shared with the workspace page) ────────────────────
  function summarize(r) {
    if (!r) return '';
    if (r.to) return (r.done ? 'התנועה עברה ל-' : 'נשלח, עוד מתעדכן: ') + r.to + ' (מ-' + r.from + ')';
    if (r.flag) return r.flag + ': ' + (r.after ? 'פעיל' : 'כבוי');
    if ('sent' in r) return r.sent ? 'נשלח' : 'השליחה נכשלה שוב';
    if (r.platform) return r.platform + ': ' + r.state;
    return JSON.stringify(r).slice(0, 300);
  }
  async function runAction(name, params, btn, out) {
    btn.disabled = true; out.textContent = 'מבצע…';
    try {
      const d = await post('/actions/' + name, { params: params || {} });
      out.textContent = '✓ בוצע. ' + summarize(d.result) + (d.undo_he ? '\nביטול: ' + d.undo_he : '');
      changed();
    } catch (e) { out.textContent = errText(e); btn.disabled = false; }
  }
  function actionCard(c) {
    const s = el('div', 'itz-card'); s.appendChild(el('div', null, '🔘 ' + c.label_he));
    if (c.reason_he) s.appendChild(el('div', 'itz-muted', c.reason_he));
    const row = el('div', 'itz-row'), out = el('div', 'itz-result');
    const b = el('button', 'itz-b ' + (c.danger ? 'danger' : 'primary'), c.button_he || 'בצע עכשיו');
    b.type = 'button'; b.onclick = () => runAction(c.action, c.params, b, out); row.appendChild(b);
    s.appendChild(row); s.appendChild(out);
    if (c.undo_he) s.appendChild(el('div', 'itz-muted', 'ביטול: ' + c.undo_he));
    return s;
  }
  function diffPre(text) {
    const pre = el('pre', 'itz-diff');
    String(text || '').split('\n').forEach(line => {
      const cls = line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@') ? 'h' : line.startsWith('+') ? 'a' : line.startsWith('-') ? 'd' : null;
      pre.appendChild(el('span', cls, line + '\n'));
    });
    return pre;
  }
  const STATUS_HE = { proposed: 'הוצע — לא נדחף לשום מקום', pr_open: 'PR פתוח — ממתין לאישור מיזוג ופריסה', merged: 'מוזג',
    deploying: 'בפריסה…', deployed: 'נפרס ✓', deploy_failed: 'הפריסה נכשלה' };
  // The Tier 3 approval card: each step asks for a NEW emailed code; the
  // server (operator_changes + operator_gate) is what checks it.
  function renderChange(ch) {
    const s = el('div', 'itz-card'); s.dataset.change = ch.id;
    s.appendChild(el('div', null, '🧩 שינוי קוד #' + ch.id + ': ' + ch.title));
    s.appendChild(el('div', 'itz-muted', STATUS_HE[ch.status] || ch.status));
    if (ch.what_he) s.appendChild(el('div', null, 'מה משתנה: ' + ch.what_he));
    if (ch.why_he) s.appendChild(el('div', null, 'למה: ' + ch.why_he));
    s.appendChild(el('div', 'itz-muted', 'קבצים: ' + (ch.files || []).join(', ')));
    if (ch.pr_url) { const a = el('a', 'itz-a', 'ה-PR ב-GitHub ←'); a.href = ch.pr_url; a.target = '_blank'; a.rel = 'noopener'; s.appendChild(a); }
    const det = el('details'); det.appendChild(el('summary', null, 'ה-diff המלא')); det.appendChild(diffPre(ch.diff)); s.appendChild(det);
    const out = el('div', 'itz-result'), row = el('div', 'itz-row');
    const step = ch.status === 'proposed' ? ['open_pr', 'אשר: פתח ענף ו-PR', '/open-pr'] : ch.status === 'pr_open' ? ['merge_deploy', 'אשר: מזג ופרוס לייצור', '/merge-deploy'] : null;
    if (step) {
      const b = el('button', 'itz-b primary', step[1] + ' (קוד במייל)'); b.type = 'button';
      b.onclick = async () => {
        b.disabled = true; out.textContent = 'שולח קוד…';
        try {
          const g = await post('/changes/' + ch.id + '/request-code', { step: step[0] });
          out.textContent = 'שלחתי קוד ל-' + g.sent_to + ' (בתוקף ' + g.expires_in_minutes + ' דקות). הקלד אותו:';
          const r2 = el('div', 'itz-row'), inp = el('input', 'itz-code'); inp.inputMode = 'numeric'; inp.maxLength = 6; inp.autocomplete = 'one-time-code';
          inp.setAttribute('aria-label', 'קוד אישור');
          const ok = el('button', 'itz-b primary', 'אשר'); ok.type = 'button';
          ok.onclick = async () => {
            ok.disabled = true;
            try { const res = await post('/changes/' + ch.id + step[2], { gate_id: g.gate_id, code: inp.value.trim() });
                  s.replaceWith(renderChange(res)); changed(); }
            catch (e) { out.textContent = errText(e); ok.disabled = false; }
          };
          r2.appendChild(inp); r2.appendChild(ok); s.appendChild(r2); inp.focus();
        } catch (e) { out.textContent = errText(e); b.disabled = false; }
      };
      row.appendChild(b);
    }
    if (ch.status === 'deploying') {
      const b = el('button', 'itz-b', 'בדוק סטטוס פריסה'); b.type = 'button';
      b.onclick = async () => { try { s.replaceWith(renderChange(await call('/changes/' + ch.id))); changed(); } catch (e) { out.textContent = errText(e); } };
      row.appendChild(b);
    }
    if (ch.deploy_run && ch.deploy_run.url) { const a = el('a', 'itz-a', 'ריצת הפריסה ←'); a.href = ch.deploy_run.url; a.target = '_blank'; a.rel = 'noopener'; row.appendChild(a); }
    s.appendChild(row); s.appendChild(out);
    return s;
  }
  // The ONE budget card: the task paused at its cost cap; "continue" is the
  // only way it goes on (core/operator_agent.ask with continue_task)
  function budgetCard(c) {
    const s = el('div', 'itz-card'); s.dataset.task = c.task_id;
    s.appendChild(el('div', null, '⏸ ' + c.label_he));
    const row = el('div', 'itz-row'), out = el('div', 'itz-result');
    const b = el('button', 'itz-b primary', c.button_he || 'המשך'); b.type = 'button';
    b.onclick = () => { b.disabled = true; continueTask(c.task_id, out); };
    row.appendChild(b); s.appendChild(row); s.appendChild(out);
    return s;
  }
  function renderCard(c) {
    if (c.type === 'action') return actionCard(c);
    if (c.type === 'budget') return budgetCard(c);
    if (c.type === 'done') { const s = el('div', 'itz-card'); s.appendChild(el('div', null, '✓ ' + c.label_he)); s.appendChild(el('div', 'itz-small', summarize(c.result))); return s; }
    if (c.type === 'change') return renderChange(c.change);
    return null;              // a report is stored as its own message
  }
  function reportText(r) {
    const lines = ['📋 ' + (r.title || ''), '', r.summary_he || '', '', 'סיבה משוערת: ' + (r.cause_he || '')];
    if ((r.evidence || []).length) lines.push('', 'ראיות:', ...r.evidence.map(e => '• ' + e));
    if (r.next_he) lines.push('', 'הלאה: ' + r.next_he);
    if ((r.owner_steps || []).length) lines.push('', 'מה רק אתה יכול לעשות:', ...r.owner_steps.map((s, i) => (i + 1) + '. ' + s));
    return lines.join('\n');
  }

  // ─── Badge + proactive decisions (pure; the tests read these) ──────────────
  function badgeText(presence) { const n = (presence && presence.badge) || 0; return n > 0 ? (n > 9 ? '9+' : String(n)) : ''; }
  function proactiveToShow(presence, shownSignature) {
    const p = presence && presence.proactive;
    return p && p.signature && p.signature !== shownSignature ? p : null;
  }

  // ─── The window ────────────────────────────────────────────────────────────
  const CSS = `
/* Tokens on the cards too: the workspace page renders approval cards outside the root */
.itz-root, .itz-card { --itz-bg:#171717; --itz-bg2:#1F1F1F; --itz-accent:#FF4C1F; --itz-accent-dim:rgba(255,76,31,.12);
  --itz-text:#F7F4EF; --itz-gray:#8A8A85; --itz-border:rgba(255,255,255,.09); --itz-green:#00C96E; --itz-red:#FF3B3B; }
.itz-root { position:fixed; left:20px; bottom:20px; z-index:900; font-family:'Heebo',system-ui,sans-serif; color:var(--itz-text);
  font-size:14px; direction:rtl; text-align:right; }
.itz-root *, .itz-root *::before, .itz-root *::after { box-sizing:border-box; }
.itz-window { width:380px; height:min(560px, calc(100vh - 40px)); background:var(--itz-bg); border:1px solid var(--itz-border);
  border-radius:18px; display:flex; flex-direction:column; overflow:hidden; box-shadow:0 20px 60px rgba(0,0,0,.55); position:relative; }
.itz-figure { display:none; }
.itz-window.minimized { width:auto; height:auto; background:transparent; border:none; box-shadow:none; overflow:visible; }
.itz-window.minimized > :not(.itz-figure) { display:none !important; }
.itz-window.minimized .itz-figure { display:flex; flex-direction:column; align-items:center; position:relative; padding-top:4px; }
.itz-stage { position:relative; display:block; height:150px; padding:0; border:0; background:none; cursor:pointer;
  font-family:inherit; color:inherit; -webkit-tap-highlight-color:transparent; }
.itz-stage::before { content:''; position:absolute; left:-18%; right:-18%; top:6%; bottom:0; border-radius:50% 50% 40% 40%;
  pointer-events:none; background:radial-gradient(ellipse at 50% 55%, rgba(255,255,255,.20), rgba(255,255,255,.06) 55%, transparent 72%); }
.itz-stage:focus-visible { outline:2px solid var(--itz-accent); outline-offset:4px; border-radius:12px; }
.itz-body { position:relative; z-index:1; display:block; height:100%; filter:drop-shadow(0 6px 10px rgba(0,0,0,.45));
  transform-origin:50% 100%; transition:transform .18s ease; }
@media (hover:hover) { .itz-stage:hover .itz-body { transform:translateY(-3px); } }
.itz-body picture, .itz-ava picture { display:block; height:100%; }
.itz-body img, .itz-ava img { display:block; height:100%; width:auto; }
.itz-ava img { width:100%; object-fit:cover; }
.itz-body.failed picture, .itz-ava.failed picture { display:none; }
.itz-body.failed::after, .itz-ava.failed::after { content:attr(data-initial); display:flex; align-items:center; justify-content:center;
  width:56px; height:56px; margin-top:60px; border-radius:50%; background:var(--itz-accent); color:#fff; font-weight:900; font-size:24px; }
.itz-ava.failed::after { width:100%; height:100%; margin:0; font-size:16px; }
.itz-shadow { position:absolute; left:50%; bottom:-3px; width:62%; height:10px; transform:translateX(-50%); border-radius:50%;
  background:radial-gradient(closest-side, rgba(0,0,0,.55), transparent); }
.itz-badge { position:absolute; top:2px; right:-4px; z-index:2; min-width:22px; height:22px; padding:0 6px; border-radius:11px;
  background:var(--itz-red); color:#fff; font-size:12px; font-weight:700; line-height:22px; text-align:center;
  box-shadow:0 0 0 2px #0D0D0D; }
.itz-badge[hidden] { display:none; }
.itz-name { margin-top:6px; font-size:11.5px; font-weight:700; background:var(--itz-bg); border:1px solid var(--itz-border);
  border-radius:100px; padding:2px 10px; white-space:nowrap; box-shadow:0 4px 12px rgba(0,0,0,.35); }
.itz-say { position:absolute; bottom:calc(100% + 6px); left:0; width:max-content; max-width:220px; background:var(--itz-bg);
  border:1px solid var(--itz-red); border-radius:12px; padding:7px 10px; font-size:12.5px; line-height:1.4; cursor:pointer;
  box-shadow:0 8px 20px rgba(0,0,0,.45); }
.itz-say[hidden] { display:none; }
.itz-head { padding:11px 12px; border-bottom:1px solid var(--itz-border); display:flex; align-items:center; gap:8px;
  background:linear-gradient(180deg,#1c1c1c,var(--itz-bg)); }
.itz-ava { width:38px; height:38px; border-radius:50%; overflow:hidden; flex-shrink:0; background:var(--itz-accent-dim); }
.itz-titles { flex:1; min-width:0; }
.itz-title { display:block; font-size:14px; font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.itz-sub { display:block; font-size:11px; color:var(--itz-green); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.itz-btn { min-width:30px; height:30px; padding:0 8px; border-radius:8px; background:var(--itz-bg2); border:1px solid var(--itz-border);
  color:var(--itz-gray); cursor:pointer; font-size:12px; font-family:inherit; display:flex; align-items:center; justify-content:center;
  flex-shrink:0; text-decoration:none; white-space:nowrap; }
.itz-btn:hover { border-color:var(--itz-accent); color:var(--itz-accent); }
.itz-attention { padding:8px 12px; border-bottom:1px solid var(--itz-border); background:rgba(255,59,59,.07); font-size:12.5px; }
.itz-attention[hidden] { display:none; }
.itz-attention div + div { margin-top:3px; }
.itz-messages { flex:1; overflow-y:auto; padding:14px; display:flex; flex-direction:column; gap:10px; overscroll-behavior:contain; }
.itz-msg { max-width:88%; padding:9px 13px; border-radius:14px; line-height:1.55; white-space:pre-wrap; word-wrap:break-word; overflow-wrap:anywhere; }
.itz-msg.owner { align-self:flex-start; background:var(--itz-accent-dim); border:1px solid rgba(255,76,31,.35); }
.itz-msg.operator { align-self:flex-end; background:var(--itz-bg2); border:1px solid var(--itz-border); }
.itz-msg.report { border-color:rgba(0,201,110,.45); }
.itz-msg.proactive { border-color:var(--itz-red); }
.itz-card { margin-top:8px; padding:9px; border-radius:10px; background:#121212; border:1px solid var(--itz-border); white-space:normal; }
.itz-muted { color:var(--itz-gray); font-size:12.5px; }
.itz-small { font-size:12px; color:var(--itz-gray); white-space:pre-wrap; overflow-wrap:anywhere; }
.itz-row { display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-top:8px; }
.itz-result { font-size:12.5px; margin-top:6px; white-space:pre-wrap; overflow-wrap:anywhere; }
.itz-b { font-family:inherit; font-size:13px; font-weight:700; border-radius:9px; padding:8px 12px; min-height:40px; cursor:pointer;
  background:var(--itz-bg2); color:var(--itz-text); border:1px solid var(--itz-border); }
.itz-b.primary { background:var(--itz-accent); border-color:var(--itz-accent); color:#fff; }
.itz-b.danger { background:var(--itz-red); border-color:var(--itz-red); color:#fff; }
.itz-b:disabled { opacity:.45; cursor:default; }
.itz-b.stop { background:transparent; border-color:var(--itz-red); color:var(--itz-red); }
.itz-b.stop[hidden] { display:none; }
.itz-a { color:var(--itz-accent); }
.itz-code { font-family:inherit; font-size:18px; letter-spacing:5px; width:8.5em; text-align:center; padding:7px; border-radius:9px;
  border:1px solid var(--itz-border); background:#0a0a0a; color:var(--itz-text); direction:ltr; }
.itz-diff { direction:ltr; text-align:left; font:11.5px/1.45 ui-monospace,Consolas,monospace; background:#0a0a0a; padding:8px;
  border-radius:8px; overflow-x:auto; max-height:300px; margin-top:6px; white-space:pre; }
.itz-diff .a { color:#7be3a7; } .itz-diff .d { color:#ff8b8b; } .itz-diff .h { color:#8ab4ff; }
.itz-root details summary { cursor:pointer; color:var(--itz-gray); font-size:12.5px; margin-top:6px; }
.itz-thinking { padding:0 14px 8px; color:var(--itz-gray); font-size:12.5px; }
.itz-thinking[hidden] { display:none; }
.itz-input { padding:10px 12px; border-top:1px solid var(--itz-border); display:flex; gap:8px; align-items:flex-end; }
.itz-input textarea { flex:1; min-width:0; font-family:inherit; font-size:14px; resize:none; height:44px; max-height:140px; padding:10px 12px;
  border-radius:12px; border:1px solid var(--itz-border); background:#0f0f0f; color:var(--itz-text); }
@media (max-width:640px) {
  .itz-root { left:8px; bottom:8px; }
  .itz-root.open { right:8px; }
  .itz-window:not(.minimized) { width:100%; height:min(82vh, calc(100vh - 16px)); }
  .itz-stage { height:68px; }
  .itz-name { display:none; }
  .itz-say { max-width:calc(100vw - 32px); }
}`;

  let root = null, win = null, parts = {}, presence = null, historyLoaded = false, pollTimer = null;

  function build() {
    const style = el('style'); style.id = 'itzik-chat-css'; style.textContent = CSS; document.head.appendChild(style);
    root = el('div', 'itz-root'); root.setAttribute('dir', 'rtl'); root.dataset.itzik = '1';
    win = el('div', 'itz-window minimized'); win.setAttribute('role', 'dialog'); win.setAttribute('aria-label', "הצ'אט עם " + NAME);

    // Minimised: the standing figure, the badge, the one proactive line
    const fig = el('div', 'itz-figure');
    const stage = el('button', 'itz-stage'); stage.type = 'button'; stage.setAttribute('aria-label', "פתיחת הצ'אט עם " + NAME);
    stage.appendChild(picture('figure', 'itz-body')); stage.appendChild(el('span', 'itz-shadow'));
    const badge = el('span', 'itz-badge'); badge.hidden = true; badge.setAttribute('aria-live', 'polite'); stage.appendChild(badge);
    const say = el('div', 'itz-say'); say.hidden = true; say.setAttribute('role', 'status');
    fig.appendChild(say); fig.appendChild(stage); fig.appendChild(el('span', 'itz-name', NAME));
    win.appendChild(fig);

    // Open: header with the portrait, attention strip, log, composer
    const head = el('div', 'itz-head');
    head.appendChild(picture('portrait', 'itz-ava'));
    const titles = el('span', 'itz-titles'); titles.appendChild(el('span', 'itz-title', NAME));
    titles.appendChild(el('span', 'itz-sub', 'השותף הטכני שלך · רק אתה רואה אותו')); head.appendChild(titles);
    const ws = el('a', 'itz-btn', '📋 הדף שלי'); ws.href = WORKSPACE; ws.title = 'דוחות, יומן פעולות, אישורים וחזרה לאחור';
    const min = el('button', 'itz-btn', '–'); min.type = 'button'; min.setAttribute('aria-label', 'מזעור');
    head.appendChild(ws); head.appendChild(min); win.appendChild(head);
    const attention = el('div', 'itz-attention'); attention.hidden = true; win.appendChild(attention);
    const log = el('div', 'itz-messages'); log.setAttribute('aria-live', 'polite'); win.appendChild(log);
    const thinking = el('div', 'itz-thinking', NAME + ' בודק — לוגים, גרסאות, התראות… (עד כ-3 דקות)'); thinking.hidden = true; win.appendChild(thinking);
    const input = el('div', 'itz-input');
    const ta = el('textarea'); ta.placeholder = 'מה קרה? למשל: האתר לא עולה'; ta.setAttribute('aria-label', 'הודעה ל' + NAME);
    const send = el('button', 'itz-b primary', 'שלח'); send.type = 'button';
    const stop = el('button', 'itz-b stop', '⏹ עצור'); stop.type = 'button'; stop.hidden = true;
    stop.setAttribute('aria-label', 'לעצור את ' + NAME + ' אחרי הצעד הנוכחי');
    input.appendChild(ta); input.appendChild(stop); input.appendChild(send); win.appendChild(input);

    root.appendChild(win); document.body.appendChild(root);
    parts = { stage, badge, say, attention, log, thinking, ta, send, stop, min };
    stop.addEventListener('click', requestStop);

    stage.addEventListener('click', open);
    say.addEventListener('click', open);
    min.addEventListener('click', minimize);
    send.addEventListener('click', sendMessage);
    ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); sendMessage(); } });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !win.classList.contains('minimized')) minimize(); });
  }

  function addMessage(role, text, cards, extraCls) {
    const box = el('div', 'itz-msg ' + (role === 'owner' ? 'owner' : 'operator') + (extraCls ? ' ' + extraCls : ''), text);
    (cards || []).forEach(c => { const n = renderCard(c); if (n) box.appendChild(n); });
    parts.log.appendChild(box);
    return box;
  }
  function scrollDown() { parts.log.scrollTop = parts.log.scrollHeight; }

  async function loadHistory() {
    let rows; try { rows = await call('/messages'); } catch (e) { return; }
    historyLoaded = true;
    // History goes ABOVE anything already in the log (a proactive line)
    const first = parts.log.firstChild;
    const frag = document.createDocumentFragment();
    const hold = parts.log; parts.log = frag;
    if (!rows.length) addMessage('operator', 'היי, אני ' + NAME + '. ספר לי מה קרה — אני בודק גרסאות ופריסות, לוגים, התראות, מצב לקוח וקוד, ואומר לך מה מצאתי, מה הראיות ומה אני ממליץ.');
    rows.forEach(m => addMessage(m.role, m.content, m.cards, m.kind === 'report' ? 'report' : ''));
    parts.log = hold; hold.insertBefore(frag, first);
    scrollDown();
  }

  async function loadPending() {
    // Approval cards live in the chat: the pending changes, newest first
    if (!presence || !(presence.items || []).some(i => i.kind === 'approval')) return;
    let d; try { d = await call('/status'); } catch (e) { return; }
    (d.changes || []).filter(ch => ch.status === 'proposed' || ch.status === 'pr_open').forEach(ch => {
      if (parts.log.querySelector('[data-change="' + CSS_ESCAPE(ch.id) + '"]')) return;
      const m = addMessage('operator', 'מחכה לאישור שלך:', [], 'pending');
      m.appendChild(renderChange(ch));
    });
    scrollDown();
  }
  function CSS_ESCAPE(s) { return String(s).replace(/[^a-zA-Z0-9_-]/g, ''); }

  function renderPresence() {
    const text = badgeText(presence);
    parts.badge.textContent = text; parts.badge.hidden = !text;
    parts.stage.setAttribute('aria-label', "פתיחת הצ'אט עם " + NAME + (text ? ' — ' + text + ' דברים מחכים לך' : ''));
    const items = (presence && presence.items) || [];
    parts.attention.textContent = '';
    items.forEach(i => parts.attention.appendChild(el('div', null, '• ' + i.text_he)));
    parts.attention.hidden = !items.length;
    const p = proactiveToShow(presence, getStore('sessionStorage', PROACTIVE_KEY));
    if (p) {
      // ONE line per session per signature: remembered before it is drawn
      setStore('sessionStorage', PROACTIVE_KEY, p.signature);
      addMessage('operator', p.text_he, [{ type: 'action', action: 'rollback', params: { revision: p.revision },
        label_he: 'חזרה לגרסה הקודמת (' + p.revision + ')', button_he: '↩ חזור עכשיו', danger: true,
        undo_he: 'כפתור "חזרה לגרסה האחרונה" בדף שלי' }], 'proactive');
      if (win.classList.contains('minimized')) { parts.say.textContent = '⚠ הגרסה החדשה מייצרת שגיאות — יש לי המלצה'; parts.say.hidden = false; }
      else scrollDown();
    }
  }

  async function refresh() {
    const since = getStore('localStorage', SEEN_KEY) || '';
    presence = await call('/presence?since=' + encodeURIComponent(since));
    renderPresence();
    return presence;
  }

  function open() {
    win.classList.remove('minimized'); root.classList.add('open'); parts.say.hidden = true;
    setStore('localStorage', SEEN_KEY, new Date().toISOString());
    const first = historyLoaded ? Promise.resolve() : loadHistory();
    first.then(loadPending).then(() => refresh().catch(() => {}));
    scrollDown();
    setTimeout(() => { try { parts.ta.focus({ preventScroll: true }); } catch (e) { /* fine */ } }, 0);
  }
  function minimize() { win.classList.add('minimized'); root.classList.remove('open'); }

  // ─── Stop: the Stop button or a stop word while he works ───────────────────
  // Same words as core/operator_agent.STOP_RX (tests compare them). The loop
  // halts BETWEEN steps; whatever already started finishes.
  const STOP_RX = /^(stop|wait|hold on|עצור|עצרי|תעצור|חכה|חכי|רגע|תפסיק|הפסק|די)[\s!.]*$/i;
  const THINKING_HE = NAME + ' בודק — לוגים, גרסאות, התראות… (אפשר לעצור בכל רגע)';
  let busy = false;
  function isStopWord(text) { return STOP_RX.test(String(text || '').trim()); }
  function setBusy(on) {
    busy = on;
    parts.stop.hidden = !on; parts.stop.disabled = false;
    parts.thinking.textContent = THINKING_HE; parts.thinking.hidden = !on;
    // the warm-up never reloads the page while a task runs (dashboard/assets/warmup.js)
    window.__uallakBusy = Math.max(0, (window.__uallakBusy || 0) + (on ? 1 : -1));
  }
  async function requestStop() {
    if (!busy) return;
    parts.stop.disabled = true; parts.thinking.textContent = 'עוצר אחרי הצעד הנוכחי…';
    try { await post('/stop', {}); }
    catch (e) { parts.thinking.textContent = errText(e); parts.stop.disabled = false; }
  }

  // One request at a time; the reply (normal / stopped / paused at the cap) renders the same way
  async function runAsk(body, ownerText) {
    setBusy(true);
    addMessage('owner', ownerText); scrollDown();
    try {
      const d = await post('/ask', body);
      const m = addMessage('operator', d.reply, d.cards);
      (d.cards || []).filter(c => c.type === 'report').forEach(c => addMessage('operator', reportText(c.report), [], 'report'));
      m.scrollIntoView({ block: 'start' });
      changed();
    } catch (e) {
      // A dropped phone connection doesn't lose the answer: it is stored server-side
      addMessage('operator', errText(e) + '\nאם החיבור נפל — התשובה נשמרת אצלי; פתח שוב בעוד דקה.');
    }
    setBusy(false);
  }

  async function sendMessage() {
    const text = parts.ta.value.trim(); if (!text) return;
    if (busy) {
      if (isStopWord(text)) { parts.ta.value = ''; addMessage('owner', text); scrollDown(); requestStop(); }
      else parts.thinking.textContent = NAME + ' באמצע משימה — "עצור" או הכפתור עוצרים אותה; ההודעה שלך מחכה כאן.';
      return;
    }
    parts.ta.value = '';
    await runAsk({ message: text }, text);
  }

  async function continueTask(taskId, out) {
    if (busy) { out.textContent = NAME + ' באמצע משימה אחרת — נסה שוב כשהיא תסתיים.'; return; }
    out.textContent = '';
    await runAsk({ message: 'המשך', continue_task: taskId }, 'המשך');
  }

  // Admin-only: draw NOTHING until the presence call proves the admin session.
  async function mount() {
    if (root) return true;
    let first;
    try {
      const since = getStore('localStorage', SEEN_KEY) || '';
      first = await call('/presence?since=' + encodeURIComponent(since));
    } catch (e) { return false; }
    build(); presence = first; renderPresence();
    pollTimer = setInterval(() => { if (!document.hidden) refresh().catch(() => {}); }, POLL_MS);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh().catch(() => {}); });
    return true;
  }

  window.ItzikChat = { mount, open, minimize, refresh, call, post, errText, summarize, runAction, renderChange,
    reportText, badgeText, proactiveToShow, isStopWord, NAME };

  const me = document.currentScript;
  if (!me || me.dataset.mount !== 'manual') {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount); else mount();
  }
})();
