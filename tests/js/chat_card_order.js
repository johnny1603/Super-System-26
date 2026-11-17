// Runs the REAL chat-window functions of dashboard/client/index.html on a tiny
// DOM, to prove a card waiting for the client is always LAST (2026-11-17).
// Used by tests/test_chat_approvals.py (CardOrderInTheBrowserCode). Prints JSON.
'use strict';
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', '..', 'dashboard', 'client', 'index.html'), 'utf8');

function extract(name) {
  const m = html.match(new RegExp('\\n(async )?function ' + name + '\\('));
  if (!m) throw new Error('missing ' + name);
  const start = m.index + 1;
  const end = html.indexOf('\n}\n', start);
  return html.slice(start, end + 2);
}

// ─── A minimal DOM ───────────────────────────────────────────────────────────
function camel(attr) { return attr.replace(/^data-/, '').replace(/-([a-z])/g, (_, c) => c.toUpperCase()); }
function matches(el, sel) {
  const m = sel.match(/^([.\w-]*)((?:\[[^\]]+\])*)$/);
  if (!m) throw new Error('selector ' + sel);
  const classes = m[1].split('.').filter(Boolean);
  if (!classes.every(c => el.classList.contains(c))) return false;
  const attrs = [...m[2].matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)];
  return attrs.every(([, a, v]) => {
    const have = a.startsWith('data-') ? el.dataset[camel(a)] : el.attrs[a];
    return v === undefined ? have !== undefined : String(have) === v;
  });
}
class ClassList {
  constructor() { this.set = new Set(); }
  add(...c) { c.forEach(x => this.set.add(x)); }
  remove(...c) { c.forEach(x => this.set.delete(x)); }
  contains(c) { return this.set.has(c); }
  toggle(c, on) { if (on === undefined) on = !this.set.has(c); on ? this.set.add(c) : this.set.delete(c); return on; }
}
class El {
  constructor(tag) { this.tagName = tag; this.children = []; this.parentNode = null; this.classList = new ClassList();
                     this.dataset = {}; this.attrs = {}; this.style = {}; this._text = ''; }
  set className(v) { this.classList = new ClassList(); String(v).split(/\s+/).filter(Boolean).forEach(c => this.classList.add(c)); }
  get className() { return [...this.classList.set].join(' '); }
  set innerHTML(v) { this.children.forEach(c => { c.parentNode = null; }); this.children = []; this._text = String(v); }
  get innerHTML() { return this._text; }
  set textContent(v) { this.innerHTML = v; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  get childNodes() { return this.children.slice(); }
  get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n === document.body; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  _detach() { if (this.parentNode) { const p = this.parentNode; p.children.splice(p.children.indexOf(this), 1); this.parentNode = null; } }
  appendChild(c) { c._detach(); c.parentNode = this; this.children.push(c); return c; }
  append(...cs) { cs.forEach(c => this.appendChild(typeof c === 'string' ? Object.assign(new El('#text'), { _text: c }) : c)); }
  prepend(...cs) {
    cs = cs.flatMap(c => (c.tagName === '#frag' ? c.childNodes : [c]));   // a fragment brings its children
    cs.reverse().forEach(c => { c._detach(); c.parentNode = this; this.children.unshift(c); });
  }
  insertBefore(c, ref) { if (!ref) return this.appendChild(c); c._detach(); c.parentNode = this; this.children.splice(this.children.indexOf(ref), 0, c); return c; }
  before(...cs) { const p = this.parentNode; cs.forEach(c => p.insertBefore(c, this)); }
  after(...cs) { const p = this.parentNode; let ref = this; cs.forEach(c => { const i = p.children.indexOf(ref); c._detach(); c.parentNode = p; p.children.splice(i + 1, 0, c); ref = c; }); }
  remove() { this._detach(); }
  contains(n) { while (n) { if (n === this) return true; n = n.parentNode; } return false; }
  _all() { return this.children.flatMap(c => [c, ...c._all()]); }
  querySelectorAll(sel) {
    if (sel.startsWith(':scope > ')) return this.children.filter(c => matches(c, sel.slice(9)));
    return this._all().filter(c => matches(c, sel));
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  closest(sel) { let n = this; while (n && n.classList) { if (matches(n, sel)) return n; n = n.parentNode; } return null; }
  scrollIntoView() {}
}
const document = { body: new El('body'), createElement: t => new El(t), createDocumentFragment: () => new El('#frag'),
                   getElementById: () => null };

// ─── The page's functions, with their non-DOM neighbours stubbed ─────────────
const CHAT_WINDOWS = {};
let server = { history: [], actions: [], all: [] };
const pendingFetches = [];
function fetch(url) {
  const body = url.includes('/api/client/actions') ? { success: true, data: server.actions }
    : url.includes('scope=all') ? { success: true, history: server.all, chat_started_at: server.boundary }
    : { success: true, history: server.history };
  const copy = JSON.parse(JSON.stringify(body));
  return new Promise(resolve => pendingFetches.push(() => resolve({ ok: true, json: async () => copy })));
}
async function flush() {
  for (let i = 0; i < 50; i++) {
    while (pendingFetches.length) pendingFetches.shift()();
    await new Promise(r => setImmediate(r));
  }
}
const sandbox = {
  document, fetch, CHAT_WINDOWS, ADMIN_VIEW: false,
  uallakI18n: { t: k => k, current: () => 'he' },
  personaMeta: () => ({ name: 'Maya', face: '', initial: 'M' }), faceHtml: () => '', esc: x => String(x),
  linkify: x => String(x), threadIntroText: () => 'hi', paintChatDays: () => {},
  stampMsg: (msg, at) => { msg.dataset.at = String(at instanceof Date ? at.toISOString() : at); },
  paintActionWhen: () => {}, paintActionProgress: () => {}, watchActionCard: () => {}, celebrateTask: () => {},
  actionDetails: () => new El('div'), decideAction: () => {},
};
const names = ['keepCardsLast', 'appendMsg', 'loadWindowHistory', 'loadWindowActions', 'appendActionCard',
               'finishActionCard', 'loadPrevInWindow'];
const src = names.map(extract).join('\n') + '\nreturn {' + names.join(',') + '};';
const fns = new Function(...Object.keys(sandbox), src)(...Object.values(sandbox));

function makeWindow(id) {
  const msgsEl = new El('div');
  document.body.appendChild(msgsEl);
  CHAT_WINDOWS[id] = { msgsEl, inputEl: new El('input') };
  return CHAT_WINDOWS[id];
}
function shape(w) {
  return w.msgsEl.children.map(c => c.classList.contains('chat-action')
    ? 'card:' + c.dataset.actionId + (c.classList.contains('awaiting') ? ':awaiting' : '')
    : (c.classList.contains('typing') ? 'typing' : (c.dataset.role || 'msg') + ':' + c.innerHTML));
}
const card = (id, status, created) => ({ id, status, can_decide: status === 'proposed', title: 'c' + id,
                                         what: '', created_at: created });

(async () => {
  const out = {};
  // 1. Reopen a window whose history spans an earlier session; the card waiting
  //    since then + one finished card. The client types while history loads.
  server = {
    history: [
      { direction: 'inbound', content: 'upload test3.mp4', created_at: '2026-11-16T09:00:00Z' },
      { direction: 'outbound', content: 'card ready', created_at: '2026-11-16T09:00:05Z' },
      { direction: 'inbound', content: 'thanks', created_at: '2026-11-17T08:30:00Z' },
      { direction: 'outbound', content: 'sure', created_at: '2026-11-17T08:30:04Z' }],
    actions: [card(7, 'proposed', '2026-11-16T09:00:05Z'), card(3, 'done', '2026-11-15T10:00:00Z')],
    all: [], boundary: '',
  };
  const w = makeWindow('media');
  const loading = fns.loadWindowHistory('media');
  fns.appendMsg(w.msgsEl, 'ok upload it', 'user');          // sent before the history arrived
  fns.appendMsg(w.msgsEl, '…', 'bot typing');
  await flush(); await loading;
  out.reopen = shape(w);
  // 2. The reply and a later background post: the waiting card stays last
  fns.appendMsg(w.msgsEl, 'which one?', 'bot');
  fns.appendMsg(w.msgsEl, 'background post', 'bot');
  out.afterReplies = shape(w);
  // 3. An older page is loaded above
  server.all = [{ direction: 'inbound', content: 'last week', created_at: '2026-11-10T09:00:00Z' }];
  server.boundary = '2026-11-16T00:00:00Z';
  const prev = fns.loadPrevInWindow('media', null);
  await flush(); await prev;
  out.afterPrevPage = shape(w);
  // 4. A second waiting card, then the same first card re-offered: one copy each, newest last
  fns.appendActionCard('media', card(9, 'proposed', '2026-11-17T09:00:00Z'));
  fns.appendActionCard('media', card(7, 'proposed', '2026-11-16T09:00:05Z'));
  out.reoffered = shape(w);
  // 5. Two reloads at once ("new chat" during a load): drawn once
  const w2 = makeWindow('general');
  server.actions = [card(11, 'proposed', '2026-11-17T07:00:00Z')];
  const a = fns.loadWindowHistory('general');
  const b = fns.loadWindowHistory('general');
  await flush(); await a; await b;
  out.doubleLoad = shape(w2);
  // 6. Decided: no longer pinned — later messages go under it
  const el7 = w.msgsEl.querySelector('.chat-action[data-action-id="7"]');
  fns.finishActionCard(el7, { status: 'failed', result: 'TikTok rejected it' });
  fns.appendMsg(w.msgsEl, 'after decision', 'bot');
  out.afterDecision = shape(w);
  out.failedClass = el7.classList.contains('failed');
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
