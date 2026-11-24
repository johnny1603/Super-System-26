"""The Media tab's ONE grid (2026-11-24, docs/MEDIA-LIBRARY.md).

What must stay true:
- one grid: every file appears once; the old "מה יש בתיקייה שלך ב-Drive"
  list, its heading and its separate selection bar are gone, and the
  "everything the system made" list never repeats a Drive file;
- every action sits on the item and is hidden where it doesn't apply: view
  (images), play (playable video), download (non-video), open in Drive, send to
  TikTok / YouTube (videos in OUR folder), and both delete modes;
- the summary, filters, search, quick filters, "בחירת הכול (N)", "ניקוי
  הבחירה", the floating bar, the ONE confirmation and the inline per-file
  result stay; the checkboxes serve every bulk action;
- selection: unchecking after "select all" removes exactly that item (the bar
  updates live); drag selection (mouse only, never from a checkbox / button /
  link / image, Ctrl adds, scrolls at the window edge); Shift-click ranges;
- the folder list: one row per connected folder plus the system folder, each
  with its name, role, connected / not reachable (with the reason), the index
  counts and an open-in-Drive link;
- no feature flag, package gate or staff-only check; the Control Room keeps
  the deletion log and the retention rule.

The UI behaviour runs the dashboard's own code under Node with a small fake
DOM (skipped when Node is not installed).
Run from the tests folder:  python test_media_tab_library.py -v
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
os.environ.setdefault("SESSION_SECRET_KEY", "test-only-not-a-secret")
for _name, _attrs in (("anthropic", {"Anthropic": lambda *a, **k: None}),
                      ("httpx", {"Response": object, "Client": object, "HTTPError": Exception,
                                 "HTTPStatusError": Exception, "TimeoutException": Exception,
                                 "RequestError": Exception, "get": None, "post": None}),
                      ("supabase", {"create_client": lambda *a, **k: None})):
    if _name not in sys.modules:
        _stub = types.ModuleType(_name)
        _stub.__dict__.update(_attrs)
        sys.modules[_name] = _stub


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


HTML = _read("dashboard", "client", "index.html")


def _media_view():
    a = HTML.index('<div class="view" id="viewMedia">')
    return HTML[a:HTML.index("<!-- /viewMedia -->", a)]


class OneGrid(unittest.TestCase):
    def test_the_old_list_and_the_old_screen_are_gone(self):
        media = _media_view()
        for gone in ('id="driveMirror"', "drive_mirror_title", 'id="driveMirrorGrid"', 'id="mlSelBar"',
                     'id="mlSearch"', 'data-i18n="media_drive_title"', 'id="mediaFolderLink"'):
            self.assertNotIn(gone, media)
        for gone in ("function renderDriveMirror", "function renderSelectionBar", "fetch('/api/client/media-library?'",
                     "driveMirror", 'id="viewLibrary"', 'data-view="library"', "showView('library')", "'library'"):
            self.assertNotIn(gone, HTML)
        self.assertEqual(media.count('class="lib-grid"'), 1)
        # the "everything the system made" list never shows a Drive file (the grid has it)
        grid = HTML[HTML.index("function renderMediaGrid()"):]
        self.assertIn("mediaAssetsCache.filter(a => !a.file_id)", grid[:grid.index("\n}\n")])

    def test_what_stays_is_still_there(self):
        media = _media_view()
        for el in ('id="libSummary"', 'id="mediaLib"', 'id="libSource"', 'id="libUsage"', 'id="libFolder"',
                   'id="libFrom"', 'id="libTo"', 'id="libMin"', 'id="libMax"', 'id="libSort"', 'id="libQ"',
                   'data-lib-preset="rejected"', 'data-lib-preset="unused_old"', 'data-lib-all="1"',
                   'data-lib-clear="1"', 'id="libSelBar"', 'id="libPreview"', 'id="libResult"', 'id="libGrid"',
                   'id="driveConnect"', 'data-i18n="ml_where_hint"', 'id="brandBox"', 'id="planBox"',
                   'id="mediaFolders"', 'id="mediaFolderRows"'):
            self.assertIn(el, media)
        self.assertLess(media.index('id="libSummary"'), media.index('id="brandBox"'))
        self.assertLess(media.index('id="libGrid"'), media.index('id="mediaFolders"'))
        self.assertIn("loadBranding();", HTML[HTML.index("async function openMediaLibrary"):][:600])
        self.assertIn("loadContentPlan();", HTML[HTML.index("async function openMediaLibrary"):][:600])

    def test_no_gate_on_it(self):
        api = _read("core", "api_server.py")
        for route in ('"/api/client/media-inventory"', '"/api/client/media-inventory/preview"',
                      '"/api/client/media-inventory/delete"', '"/api/client/media-inventory/thumb/{file_id}"'):
            body = api[api.index(route):]
            body = body[:body.index("\n@app.")]
            self.assertIn("_require_session(request)", body)
            for gate in ("_require_admin", "feature", "package", "plan_", "staff", "is_admin", "flag"):
                self.assertNotIn(gate, body)

    def test_the_control_room_keeps_the_log_and_the_rule(self):
        admin = _read("dashboard", "admin", "index.html")
        self.assertIn("🗂 מאגר המדיה — כלל שמירה ויומן מחיקות", admin)
        self.assertIn("crMlOn", admin)


class Server(unittest.TestCase):
    """Action hints, select-all minus exclusions, and the folder list's counts."""

    def setUp(self):
        from core import drive_folders, media_inventory as mi
        self.mi, self.df = mi, drive_folders
        self.saved = (drive_folders.tree, drive_folders.share_with)
        drive_folders.share_with = lambda: "agent@example.iam.gserviceaccount.com"
        drive_folders.tree = lambda cid: [
            {"id": "dcA", "folder_id": "FOLDER_A", "folder_url": "https://drive.google.com/drive/folders/FOLDER_A",
             "display_name": "מוצרים קיץ", "role": "product_catalog", "role_text": "קטלוג מוצרים",
             "status": "ok", "error_text": "", "excluded": False},
            {"id": "dcB", "folder_id": "FOLDER_B", "folder_url": "https://drive.google.com/drive/folders/FOLDER_B",
             "display_name": "Avatar", "role": "raw_materials", "role_text": "חומרי גלם",
             "status": "no_access", "error_text": "עוד לא שיתפתם איתנו את התיקייה. …", "excluded": False}]

    def tearDown(self):
        self.df.tree, self.df.share_with = self.saved

    def item(self, fid, origin, kind, root="", indexed=True, size=100, mime=""):
        return {"id": fid, "name": fid, "origin": origin, "source": "uploaded" if origin == "media" else "client_asset",
                "kind": kind, "mime": mime or {"image": "image/jpeg", "video": "video/mp4"}.get(kind, "application/pdf"),
                "folder": "", "folder_keys": [], "created": "2026-11-20", "size": size, "description": "", "tags": [],
                "root_id": root, "indexed": indexed, "usage": "unused", "used_in": [], "web_view_link": ""}

    def test_the_folder_list_counts_what_the_index_holds(self):
        items = [self.item("m1", "media", "image"), self.item("m2", "media", "video"),
                 self.item("a1", "connected", "image", "dcA"), self.item("a2", "connected", "image", "dcA"),
                 self.item("a3", "connected", "video", "dcA"),
                 self.item("a4", "connected", "image", "dcA", indexed=False)]     # pending: not counted
        rows = self.mi.folder_list(27, items)
        self.assertEqual([r["id"] for r in rows], ["system", "dcA", "dcB"])
        system, a, b = rows
        self.assertEqual((system["indexed"], system["images"], system["videos"]), (2, 1, 1))
        self.assertEqual((a["indexed"], a["images"], a["videos"]), (3, 2, 1))
        self.assertEqual((a["name"], a["role_text"], a["reachable"], a["reason"]), ("מוצרים קיץ", "קטלוג מוצרים", True, ""))
        self.assertEqual(a["url"], "https://drive.google.com/drive/folders/FOLDER_A")
        self.assertEqual((b["reachable"], b["reason"]), (False, "עוד לא שיתפתם איתנו את התיקייה. …"))
        self.assertEqual((b["indexed"], b["images"], b["videos"]), (0, 0, 0))

    def test_action_hints(self):
        pub = self.mi._public(self.item("v1", "media", "video", size=10), play_max=1000, sent={"v1": ["tiktok"]})
        self.assertEqual((pub["playable"], pub["sendable"], pub["sent"]), (True, True, ["tiktok"]))
        pub = self.mi._public(self.item("v2", "connected", "video", size=10), play_max=1000)
        self.assertEqual((pub["playable"], pub["sendable"]), (True, False))           # not our folder: no send
        self.assertEqual(pub["web_view_link"], "https://drive.google.com/file/d/v2/view")
        pub = self.mi._public(self.item("v3", "media", "video", size=5000), play_max=1000)
        self.assertFalse(pub["playable"])
        pub = self.mi._public(self.item("i1", "connected", "image"), play_max=1000)
        self.assertEqual((pub["previewable"], pub["download"]), (True, "file"))

    def test_select_all_minus_the_unchecked(self):
        items = [self.item(x, "media", "image") for x in ("a", "b", "c")]
        chosen, _ = self.mi.select(27, None, {}, items=items, exclude=["b"])
        self.assertEqual([i["id"] for i in chosen], ["a", "c"])
        api = _read("core", "api_server.py")
        self.assertIn("req.mode, req.exclude)", api)
        self.assertIn('by="client", exclude=req.exclude)', api)


HARNESS = r"""
const vm = require('vm');
const fs = require('fs');
const block = fs.readFileSync(process.argv[2], 'utf8');
function check(cond, msg) { if (!cond) { throw new Error(msg); } }
const els = {};
const listeners = {};
function classes() { const s = new Set(); return { set: s, add: c => s.add(c), remove: c => s.delete(c),
  toggle: (c, on) => (on === undefined ? (s.has(c) ? s.delete(c) : s.add(c)) : (on ? s.add(c) : s.delete(c))),
  contains: c => s.has(c) }; }
let CARDS = [];
function el(id) {
  return els[id] || (els[id] = { id, innerHTML: '', textContent: '', hidden: false, value: '', dataset: {},
    placeholder: '', disabled: false, classList: classes(), scrollIntoView() {}, offsetParent: {},
    getBoundingClientRect: () => ({ left: 0, top: 200 - win.scrollY, right: 320, bottom: 410 - win.scrollY }),
    querySelectorAll(sel) { return id === 'libGrid' && sel === '.lib-card' ? CARDS : []; },
    querySelector(sel) { const m = /data-id="([^"]+)"/.exec(sel); return id === 'libGrid' && m ? CARDS.find(c => c.dataset.id === m[1]) || null : null; } });
}
// six cards, 3 per row, 100x100 with a 10 px gap, the grid starting at y=200
function layCards(items) {
  CARDS = items.map((it, n) => {
    const left = (n % 3) * 110, top = 200 + Math.floor(n / 3) * 110;
    const box = { checked: false };
    return { dataset: { id: it.id }, classList: classes(), box, getAttribute: k => (k === 'data-id' ? it.id : null),
             querySelector: () => box,
             getBoundingClientRect: () => ({ left, top: top - win.scrollY, right: left + 100, bottom: top + 100 - win.scrollY }) };
  });
}
const calls = [], downloads = [];
const win = { location: {}, scrollX: 0, scrollY: 0, innerHeight: 800, coarse: false,
  scrollBy(dx, dy) { this.scrollY += dy; }, matchMedia() { return { matches: win.coarse }; } };
const created = [];
globalThis.window = win;
globalThis.document = { getElementById: el, addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
  createElement() { const d = { style: {}, className: '', removed: false, remove() { this.removed = true; } }; created.push(d); return d; },
  body: { appendChild() {} }, documentElement: {} };
globalThis.esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
globalThis.uallakI18n = { t: k => k };
globalThis.TIKTOK_CONNECTED = true;
globalThis.showNotice = () => { throw new Error('a toast instead of the inline result'); };
globalThis.uallakMediaViewer = { download: async (f) => { downloads.push(f.id); }, open() {} };
globalThis.mediaViewerOpts = () => ({ notify() {} });
// defined outside the library block (the send / download / player helpers)
const sends = [];
globalThis.mlDownloadFiles = files => files.forEach(f => downloads.push(f.id));
globalThis.mlSendFiles = (target, files) => sends.push(target + ':' + files.map(f => f.id).join(','));
globalThis.sendVideoToTiktok = id => sends.push('tile:' + id);
globalThis.playVideo = f => sends.push('play:' + f.id);
const MB = 1048576;
const ITEMS = [
  { id: 'A', name: 'a.jpg', size: 1 * MB, source: 'uploaded', usage: 'used', kind: 'image', origin: 'media', folder: '',
    created: '2026-11-01', download: 'file', previewable: true, playable: false, sendable: false,
    web_view_link: 'https://drive.google.com/file/d/A/view', used_in: [{ kind: 'post', title: 'פוסט קיץ', status: 'approved' }] },
  { id: 'B', name: 'b.mp4', size: 2 * MB, source: 'generated_video', usage: 'unused', kind: 'video', origin: 'media', folder: '',
    created: '2026-11-02', download: 'file', previewable: false, playable: true, sendable: true, web_view_link: 'https://drive.google.com/file/d/B/view', used_in: [] },
  { id: 'C', name: 'c.mp4', size: 3 * MB, source: 'client_asset', usage: 'unused', kind: 'video', origin: 'connected', folder: 'Avatar',
    created: '2026-11-03', download: 'file', previewable: false, playable: true, sendable: false, web_view_link: 'https://drive.google.com/file/d/C/view', used_in: [] },
  { id: 'D', name: 'brief', size: 4 * MB, source: 'uploaded', usage: 'unused', kind: 'document', origin: 'media', folder: '',
    created: '2026-11-04', download: 'export', previewable: false, playable: false, sendable: false, web_view_link: 'https://docs.google.com/document/d/D', used_in: [] },
  { id: 'E', name: 'e.jpg', size: 5 * MB, source: 'client_asset', usage: 'unused', kind: 'image', origin: 'connected', folder: 'מוצרים',
    created: '2026-11-05', download: 'file', previewable: true, playable: false, sendable: false, web_view_link: 'https://drive.google.com/file/d/E/view', used_in: [] },
  { id: 'F', name: 'f.mov', size: 6 * MB, source: 'generated_video', usage: 'unused', kind: 'video', origin: 'media', folder: '',
    created: '2026-11-06', download: 'file', previewable: false, playable: false, sendable: true, web_view_link: 'https://drive.google.com/file/d/F/view', used_in: [] },
];
const TOTAL = 21 * MB;
const FOLDERS = [{ system: true, id: 'system', indexed: 4, images: 1, videos: 2 },
  { system: false, id: 'dcA', name: 'מוצרים קיץ', role: 'product_catalog', role_text: 'קטלוג מוצרים', reachable: true, reason: '',
    url: 'https://drive.google.com/drive/folders/FOLDER_A', indexed: 1, images: 1, videos: 0, excluded: false },
  { system: false, id: 'dcB', name: 'Avatar', role: 'raw_materials', role_text: 'חומרי גלם', reachable: false,
    reason: 'עוד לא שיתפתם איתנו את התיקייה.', url: 'https://drive.google.com/drive/folders/FOLDER_B', indexed: 1, images: 0, videos: 1, excluded: false }];
let answers = {};
globalThis.fetch = async (url, opts) => {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  calls.push({ url, body });
  const path = url.split('?')[0];
  const data = path === '/api/client/media-inventory'
    ? { items: ITEMS, summary: { count: 6, size: TOTAL, by_source: { uploaded: { count: 2, size: 5 * MB } } },
        matching: { count: 6, size: TOTAL }, folders: [], has_more: false, settings: {}, folder_list: FOLDERS, download_max_mb: 100 }
    : answers[path];
  return { status: 200, json: async () => ({ success: true, data }) };
};
vm.runInThisContext(block + '\n;globalThis.__lib = { libUI, LIB_T, invLoad, libDragScroll, libRenderFolders, libCount };');
const { libUI, LIB_T, invLoad, libDragScroll, libRenderFolders, libCount } = globalThis.__lib;
const tick = async () => { for (let i = 0; i < 8; i++) await new Promise(r => setTimeout(r, 0)); };
const fire = (type, ev) => (listeners[type] || []).forEach(fn => fn(ev));
const ids = () => [...(libUI.allMatching ? ITEMS.map(i => i.id).filter(i => !libUI.excluded.has(i)) : libUI.selected.keys())].sort().join('');
function tickBox(id, checked, shift) {
  const box = { checked, getAttribute: k => (k === 'data-lib-sel' ? id : null) };
  fire('click', { shiftKey: Boolean(shift), target: { closest: s => (s === '#mediaLib [data-lib-sel]' ? box : null) } });
}
function click(attrs) {
  const btn = { id: attrs.id || '', disabled: false, textContent: '', hasAttribute: a => a in attrs, getAttribute: a => attrs[a] };
  fire('click', { target: { closest: s => (s === '#mediaLib button' ? btn : null) } });
}
// a press: on empty grid space, or on a control
function press(x, y, opts) {
  opts = opts || {};
  const target = { closest: s => (s === '#libGrid' ? {} : (opts.on && s.startsWith('input') ? {} : null)) };
  fire('mousedown', { button: 0, clientX: x, clientY: y, ctrlKey: Boolean(opts.ctrl), metaKey: false, target });
}
const move = (x, y) => fire('mousemove', { clientX: x, clientY: y, preventDefault() {} });
const release = (ctrl) => fire('mouseup', { ctrlKey: Boolean(ctrl) });
const drag = (x0, y0, x1, y1, ctrl) => { press(x0, y0, { ctrl }); move(x0 + 3, y0 + 3); move(x1, y1); release(ctrl); };
// the number the bar shows (explicit: "נבחרו: <b>n</b>"; select-all: "נבחרו כל n …")
const barN = () => {
  const html = el('libSelBar').innerHTML, n = libCount().n;
  const shown = libUI.allMatching ? html.includes(LIB_T.allChosen.replace('{n}', n)) : html.includes('<b>' + n + '</b>');
  return el('libSelBar').hidden || !shown ? -1 : n;
};

(async () => {
  await invLoad(true);
  layCards(ITEMS);
  const grid = el('libGrid').innerHTML;

  // ONE grid: each file once
  ITEMS.forEach(i => check((grid.match(new RegExp('class="lib-card[^"]*" data-id="' + i.id + '"', 'g')) || []).length === 1, 'once: ' + i.id));
  check((grid.match(/type="checkbox" data-lib-sel=/g) || []).length === 6, 'a checkbox on every item');

  // every action on the right item types, hidden where it doesn't apply
  const card = id => { const s = grid.indexOf('data-id="' + id + '"'); const e = grid.indexOf('<div class="lib-card', s);
                       return grid.slice(s, e === -1 ? grid.length : e); };
  const acts = id => (card(id).match(/data-lib-act="(\w+)"/g) || []).map(a => a.slice(14, -1)).sort().join(',');
  check(acts('A') === 'dl,view', 'image in our folder: view + download, got ' + acts('A'));
  check(acts('B') === 'play,tiktok,youtube', 'playable video in our folder, got ' + acts('B'));
  check(acts('C') === 'play', 'video in a connected folder: play only (no send), got ' + acts('C'));
  check(acts('D') === 'dl' && card('D').includes(LIB_T.actDownloadPdf), 'a Google Doc: download as PDF, got ' + acts('D'));
  check(acts('E') === 'dl,view', 'image in a connected folder, got ' + acts('E'));
  check(acts('F') === 'tiktok,youtube', 'a video too big to play: send only, got ' + acts('F'));
  ITEMS.forEach(i => {
    check(card(i.id).includes('data-lib-one="index"') && card(i.id).includes('data-lib-one="file"'), 'both deletes on ' + i.id);
    check(card(i.id).includes('href="' + i.web_view_link + '"'), 'open in Drive on ' + i.id);
  });
  check(!card('A').includes('disabled'), 'nothing shown disabled');

  // bulk actions work on the merged selection
  tickBox('A', true); tickBox('B', true);
  let bar = el('libSelBar').innerHTML;
  check(!el('libSelBar').hidden && barN() === 2 && bar.includes('3 MB'), 'two selected: count + size');
  check(bar.includes('data-lib-bulk-act="dl"') && bar.includes('data-lib-bulk-act="tiktok"') && bar.includes('data-lib-bulk-act="youtube"'),
        'download + both sends for this selection');
  check(bar.includes('data-lib-bulk="index"') && bar.includes('data-lib-bulk="file"'), 'both deletes in the bar');
  click({ 'data-lib-bulk-act': 'dl' }); await tick();
  check(downloads.join() === 'A', 'bulk download takes the downloadable ones only');
  click({ 'data-lib-bulk-act': 'youtube' }); click({ 'data-lib-bulk-act': 'tiktok' });
  check(sends.join() === 'youtube:B,tiktok:B', 'bulk sends take the sendable videos only, got ' + sends.join());
  click({ 'data-lib-act': 'play', 'data-id': 'B' }); click({ 'data-lib-act': 'tiktok', 'data-id': 'B' });
  check(sends.slice(-2).join() === 'play:B,tile:B', 'the item actions');

  // select all, then uncheck one: exactly that one goes, the bar updates live
  click({ 'data-lib-all': '1' });
  check(libUI.allMatching && barN() === 6 && el('libSelBar').innerHTML.includes('21 MB'), 'select all = 6');
  tickBox('C', false);
  check(barN() === 5 && el('libSelBar').innerHTML.includes('18 MB') && ids() === 'ABDEF', 'unchecking C after select-all');
  check(CARDS.find(c => c.dataset.id === 'C').classList.contains('sel') === false, 'its card repaints');
  answers['/api/client/media-inventory/preview'] = { count: 5, size: 18 * MB, folders: [], affected_posts: [] };
  click({ 'data-lib-bulk': 'index' }); await tick();
  let prev = calls.filter(c => c.url.endsWith('/preview')).pop();
  check(prev.body.mode === 'index' && prev.body.exclude.join() === 'C' && prev.body.filter, 'select-all minus C is sent');
  tickBox('C', true);
  check(barN() === 6, 'checking it again');
  click({ 'data-lib-clear': '1' });
  check(el('libSelBar').hidden && ids() === '', 'clear selection');

  // drag selection: everything the rectangle touches, nothing outside
  drag(5, 190, 215, 260);
  check(ids() === 'AB', 'drag over A and B selects exactly them, got ' + ids());
  check(created.length && created[created.length - 1].removed, 'the rectangle is drawn, then removed');
  drag(225, 320, 300, 330, true);
  check(ids() === 'ABF', 'Ctrl-drag adds F, got ' + ids());
  drag(115, 320, 150, 340);
  check(ids() === 'E', 'a plain drag replaces, got ' + ids());

  // a press on a checkbox / button never starts a marquee
  const before = created.length;
  press(20, 220, { on: true }); move(60, 260); move(250, 380); release();
  check(created.length === before && ids() === 'E', 'no marquee from a control');

  // a press BESIDE the grid — on the page background, an element that CONTAINS
  // the grid — at the grid's height starts the rectangle (the real-browser bug:
  // the cards fill the grid, so the empty space next to it is where people press)
  const bg = { closest: () => null, contains: n => n === el('libGrid') };
  const pressOn = (target, x, y, ctrl) => fire('mousedown', { button: 0, clientX: x, clientY: y, ctrlKey: Boolean(ctrl), metaKey: false, target });
  click({ 'data-lib-clear': '1' });
  pressOn(bg, 400, 250); move(390, 252); move(150, 260); release();
  check(ids() === 'BC', 'a drag from beside the grid selects what it touches, got ' + ids());
  pressOn(bg, 400, 150); move(390, 160); move(150, 260); release();
  check(ids() === 'BC', 'above the grid\'s height it does not start');
  pressOn({ closest: () => null, contains: () => false }, 400, 250); move(390, 252); move(5, 260); release();
  check(ids() === 'BC', 'an element that does not contain the grid (a header, a panel) does not start it');
  el('libGrid').offsetParent = null;
  pressOn(bg, 400, 250); move(390, 252); move(5, 260); release();
  check(ids() === 'BC', 'nothing while the Media tab is hidden');
  el('libGrid').offsetParent = {};
  pressOn(bg, 400, 250, true); move(390, 252); move(50, 260); release(true);
  check(ids() === 'ABC', 'Ctrl from beside the grid adds, got ' + ids());
  click({ 'data-lib-clear': '1' }); tickBox('E', true);

  // a short press (a click) is not a drag
  press(5, 190); move(6, 191); release();
  check(ids() === 'E', 'a click on empty space changes nothing');

  // near the bottom edge it scrolls
  press(5, 190); move(20, 400); move(20, 790);
  const y = win.scrollY; libDragScroll(); libDragScroll();
  check(win.scrollY > y, 'it scrolls at the bottom edge');
  release(); win.scrollY = 0;

  // touch: no marquee at all
  win.coarse = true; click({ 'data-lib-clear': '1' });
  drag(5, 190, 215, 260);
  check(ids() === '', 'no drag selection on touch');
  win.coarse = false;

  // Shift-click: the range between the two
  tickBox('B', true); tickBox('E', true, true);
  check(ids() === 'BCDE', 'shift-click B → E selects B..E, got ' + ids());
  tickBox('C', false, false);
  check(ids() === 'BDE', 'and each one still toggles');
  click({ 'data-lib-clear': '1' });

  // filters behave as before: a change reloads with the filter, the selection resets
  tickBox('A', true);
  el('libSource').value = 'client_asset';
  fire('change', { target: { closest: s => (s === '#mediaLib' ? {} : null), matches: s => s === '.lib-filter', id: 'libSource', dataset: {} } });
  await tick();
  check(calls.filter(c => c.url.startsWith('/api/client/media-inventory?')).pop().url.includes('source=client_asset'), 'filter sent');
  check(ids() === '' && el('libSelBar').hidden, 'a new filter clears the selection');
  el('libSource').value = '';

  // the ONE confirmation, the usage notice, the per-file result
  tickBox('A', true); tickBox('E', true);
  answers['/api/client/media-inventory/preview'] = { count: 2, size: 6 * MB, folders: [],
    affected_posts: [{ id: 7, title: 'פוסט קיץ', status: 'approved', planned_date: '2026-11-30' }] };
  answers['/api/client/media-inventory/delete'] = { done: 1, size: 1 * MB, trashed: 0, failed: [{ id: 'E', name: 'e.jpg', reason: 'owner_only' }] };
  click({ 'data-lib-bulk': 'file' }); await tick();
  prev = calls.filter(c => c.url.endsWith('/preview')).pop();
  check(JSON.stringify(prev.body) === JSON.stringify({ mode: 'file', ids: ['A', 'E'] }), 'preview of the selection');
  const box = el('libPreview').innerHTML;
  check(box.includes(LIB_T.explainFile) && box.includes('<b>2</b>') && box.includes('6 MB'), 'count + size');
  check(box.includes('פוסט קיץ') && box.includes(LIB_T.affected.replace('{n}', 1)), 'the usage notice');
  check((box.match(/data-lib-run=/g) || []).length === 1 && !calls.some(c => c.url.endsWith('/delete')), 'one confirmation, nothing deleted yet');
  click({ 'data-lib-run': '1' }); await tick();
  const del = calls.filter(c => c.url.endsWith('/delete'));
  check(del.length === 1 && del[0].body.mode === 'file' && del[0].body.ids.join() === 'A,E', 'one delete');
  const res = el('libResult').innerHTML;
  check(res.includes(LIB_T.doneFile.replace('{n}', 1).replace('{size}', '1 MB')), 'how many went');
  check(res.includes('<li><bdi dir="auto">e.jpg</bdi> — ' + esc(LIB_T.reason_owner_only) + '</li>'), 'per-file reason');

  // single item, remove from the library
  answers['/api/client/media-inventory/preview'] = { count: 1, size: 1 * MB, folders: [], affected_posts: [] };
  answers['/api/client/media-inventory/delete'] = { done: 1, size: 1 * MB, trashed: 0, failed: [] };
  click({ 'data-lib-one': 'index', 'data-id': 'A' }); await tick();
  check(JSON.stringify(calls.filter(c => c.url.endsWith('/preview')).pop().body) === JSON.stringify({ mode: 'index', ids: ['A'] }), 'single index');
  click({ 'data-lib-run': '1' }); await tick();
  check(el('libResult').innerHTML.includes(LIB_T.doneIndex.replace('{n}', 1).replace('{size}', '1 MB')), 'index result');

  // the folder list: the system folder + one row per connected folder
  vm.runInThisContext("mediaFolderInfo = { link: 'https://drive.google.com/drive/folders/SYSTEM', status: 'ok' };");
  libRenderFolders();
  const rows = el('mediaFolderRows').innerHTML.split('<div class="lib-frow').slice(1);
  check(rows.length === 3, 'system + 2 connected, got ' + rows.length);
  check(rows[0].includes(LIB_T.fSystemName) && rows[0].includes('href="https://drive.google.com/drive/folders/SYSTEM"'), 'the system folder, with its link');
  check(rows[1].includes('מוצרים קיץ') && rows[1].includes('קטלוג מוצרים') && rows[1].includes(LIB_T.fConnected)
        && rows[1].includes('href="https://drive.google.com/drive/folders/FOLDER_A"') && rows[1].includes('target="_blank"'), 'a connected folder');
  check(rows[1].includes(LIB_T.fCounts.replace('{n}', 1).replace('{img}', 1).replace('{vid}', 0)), 'its counts');
  check(rows[2].startsWith(' bad') && rows[2].includes(LIB_T.fUnreachable) && rows[2].includes('עוד לא שיתפתם איתנו את התיקייה.')
        && rows[2].includes('href="https://drive.google.com/drive/folders/FOLDER_B"'), 'unreachable: its reason, still its link');
  check(rows[0].includes(LIB_T.fCounts.replace('{n}', 4).replace('{img}', 1).replace('{vid}', 2)), 'the system counts');
  vm.runInThisContext("mediaFolderInfo = { link: '', status: 'not_configured' };");
  libRenderFolders();
  check(el('mediaFolderRows').innerHTML.split('<div class="lib-frow')[1].includes('media_drive_not_ready'), 'no system link: says why');
  console.log('OK');
})().catch(e => { console.error(e.message); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class Behaviour(unittest.TestCase):
    def test_the_grid_selection_drag_shift_and_folders(self):
        a = HTML.index("const libUI = {")
        block = HTML[a:HTML.index("// ═", HTML.index("// CONNECTED DRIVE FOLDERS") - 300)]
        with tempfile.TemporaryDirectory() as tmp:
            src, harness = os.path.join(tmp, "lib.js"), os.path.join(tmp, "harness.js")
            with open(src, "w", encoding="utf-8") as f:
                f.write(block)
            with open(harness, "w", encoding="utf-8") as f:
                f.write(HARNESS)
            run = subprocess.run(["node", harness, src], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        self.assertIn("OK", run.stdout)


if __name__ == "__main__":
    unittest.main()
