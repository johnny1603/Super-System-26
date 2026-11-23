"""Media deletion lives IN the Media tab (2026-11-23, docs/MEDIA-LIBRARY.md).

What must stay true:
- the separate "מאגר המדיה" screen, its side-nav item and the Media-tab
  header button that linked to it are gone — one place, the Media tab;
- the Media tab has the storage summary at the top and a library section with
  the filters (source / usage / connected folder / date / size), "select all",
  "clear selection", a checkbox and both delete buttons on every item, and a
  selection bar showing the count and total size with both actions;
- ONE confirmation (count, size, the waiting / scheduled posts that use the
  items), then the delete, then the result inline with every failed file and
  its plain Hebrew reason;
- no feature flag, package gate or staff-only check on it;
- the Control Room keeps the deletion log and the retention rule.

The UI behaviour runs the dashboard's own library code under Node with a
small fake DOM (skipped when Node is not installed).
Run from the tests folder:  python test_media_tab_library.py -v
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


HTML = _read("dashboard", "client", "index.html")


def _media_view():
    a = HTML.index('<div class="view" id="viewMedia">')
    return HTML[a:HTML.index("<!-- /viewMedia -->", a)]


class OnePlace(unittest.TestCase):
    def test_the_separate_screen_and_its_nav_entry_are_gone(self):
        for gone in ('id="viewLibrary"', 'data-view="library"', "showView('library')", "'library'",
                     "מה תופס מקום ומחיקה", "#viewLibrary", "libPresetFolder"):
            self.assertNotIn(gone, HTML)
        views = re.search(r"const VIEWS = \[([^\]]*)\]", HTML).group(1)
        self.assertNotIn("library", views)
        for path in (("core", "feature_catalog.py"), ("docs", "MEDIA-LIBRARY.md"), ("CLAUDE.md",)):
            text = _read(*path)
            self.assertNotIn("תפריט הצד, ׳מאגר המדיה׳", text)
            self.assertNotIn('side nav "🗂 מאגר המדיה"', text)

    def test_everything_sits_in_the_media_tab(self):
        media = _media_view()
        for el in ('id="libSummary"', 'id="mediaLib"', 'id="libSource"', 'id="libUsage"', 'id="libFolder"',
                   'id="libFrom"', 'id="libTo"', 'id="libMin"', 'id="libMax"', 'data-lib-all="1"',
                   'data-lib-clear="1"', 'id="libSelBar"', 'id="libPreview"', 'id="libResult"', 'id="libGrid"'):
            self.assertIn(el, media)
        for source in ('value="generated_image,generated_video,slide"', 'value="real_copy"', 'value="client_asset"'):
            self.assertIn(source, media)
        self.assertLess(media.index('id="libSummary"'), media.index('id="brandBox"'))   # at the top
        self.assertLess(media.index('id="mediaLib"'), media.index('id="driveMirror"'))
        self.assertIn("if (name === 'media') { libReadFilter(); invLoad(true); }", HTML)
        # the Drive list owns loadLibrary; the library has its own loader (they used to collide)
        self.assertEqual(len(re.findall(r"function loadLibrary\(", HTML)), 1)
        self.assertEqual(len(re.findall(r"function invLoad\(", HTML)), 1)

    def test_no_gate_on_it(self):
        api = _read("core", "api_server.py")
        for route in ('"/api/client/media-inventory"', '"/api/client/media-inventory/preview"',
                      '"/api/client/media-inventory/delete"', '"/api/client/media-inventory/thumb/{file_id}"'):
            body = api[api.index(route):]
            body = body[:body.index("\n@app.")]
            self.assertIn("_require_session(request)", body)
            for gate in ("_require_admin", "feature", "package", "plan_", "staff", "is_admin", "flag"):
                self.assertNotIn(gate, body)
        src = _read("core", "media_inventory.py")
        for gate in ("feature_flags", "package", "is_staff", "require_admin"):
            self.assertNotIn(gate, src)

    def test_the_control_room_keeps_the_log_and_the_rule(self):
        admin = _read("dashboard", "admin", "index.html")
        self.assertIn("🗂 מאגר המדיה — כלל שמירה ויומן מחיקות", admin)
        self.assertIn("/api/admin/media-inventory/", admin)
        self.assertIn("crMlOn", admin)


HARNESS = r"""
const vm = require('vm');
const fs = require('fs');
const block = fs.readFileSync(process.argv[2], 'utf8');
const els = {};
const listeners = {};
function el(id) {
  return els[id] || (els[id] = { id, innerHTML: '', textContent: '', hidden: false, value: '', dataset: {},
    placeholder: '', disabled: false, querySelectorAll() { return []; }, scrollIntoView() {},
    classList: { toggle() {} } });
}
const calls = [];
let driveRefreshed = 0;
const ITEMS = [
  { id: 'A', name: 'a.jpg', size: 1048576, source: 'generated_image', usage: 'used', kind: 'image', origin: 'media',
    folder: '', created: '2026-11-01', used_in: [{ kind: 'post', title: 'פוסט קיץ', status: 'approved' }] },
  { id: 'B', name: 'b.jpg', size: 2097152, source: 'client_asset', usage: 'unused', kind: 'image', origin: 'client',
    folder: 'מוצרים', created: '2026-11-02', used_in: [] },
];
let answers = {};
globalThis.document = { getElementById: el, addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); } };
globalThis.window = { location: {} };
globalThis.esc = s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
globalThis.showNotice = () => { throw new Error('a toast instead of the inline result'); };
globalThis.loadLibrary = () => { driveRefreshed++; };
globalThis.fetch = async (url, opts) => {
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  calls.push({ url, body });
  const path = url.split('?')[0];
  const data = path === '/api/client/media-inventory'
    ? { items: ITEMS, summary: { count: 2, size: 3145728, by_source: { generated_image: { count: 1, size: 1048576 },
        client_asset: { count: 1, size: 2097152 } } }, matching: { count: 2, size: 3145728 },
        folders: [{ key: 'client:F1', label: 'מוצרים', origin: 'client', count: 1 }], has_more: false, settings: {} }
    : answers[path];
  return { status: 200, json: async () => ({ success: true, data }) };
};
vm.runInThisContext(block + '\n;globalThis.__lib = { libUI, LIB_T, invLoad };');
const { libUI, LIB_T, invLoad } = globalThis.__lib;
const tick = async () => { for (let i = 0; i < 8; i++) await new Promise(r => setTimeout(r, 0)); };
function check(cond, msg) { if (!cond) { throw new Error(msg); } }
function change(id, size, checked) {
  const target = { checked, matches: s => s === '[data-lib-sel]', id: '',
    closest: s => (s === '#mediaLib' ? {} : { classList: { toggle() {} } }),
    getAttribute: k => ({ 'data-lib-sel': id, 'data-size': String(size) })[k] };
  listeners.change.forEach(fn => fn({ target }));
}
function click(attrs) {
  const btn = { id: attrs.id || '', disabled: false, textContent: '',
    hasAttribute: a => a in attrs, getAttribute: a => attrs[a] };
  const target = { closest: s => (s === '#mediaLib button' ? btn : null) };
  listeners.click.forEach(fn => fn({ target }));
  return btn;
}
(async () => {
  await invLoad(true);
  const grid = el('libGrid').innerHTML;
  check((grid.match(/type="checkbox" data-lib-sel=/g) || []).length === 2, 'a checkbox on every item');
  check((grid.match(/data-lib-one="index"/g) || []).length === 2 && (grid.match(/data-lib-one="file"/g) || []).length === 2,
        'both delete buttons on every item');
  check(grid.includes(LIB_T.oneIndex) && grid.includes(LIB_T.oneFile), 'each button says what it does');
  check(el('libSummary').innerHTML.includes('3 MB') && el('libSummary').innerHTML.includes(LIB_T.src_client_asset),
        'the storage summary by source');
  check(el('libSelBar').hidden, 'no bar before anything is selected');

  change('A', 1048576, true);
  check(!el('libSelBar').hidden && el('libSelBar').innerHTML.includes('<b>1</b>') && el('libSelBar').innerHTML.includes('1 MB'),
        'one selected: count and size');
  change('B', 2097152, true);
  const bar = el('libSelBar').innerHTML;
  check(bar.includes('<b>2</b>') && bar.includes('3 MB'), 'two selected: count and total size');
  check(bar.includes(LIB_T.btnIndex) && bar.includes(LIB_T.btnFile), 'both bulk actions, saying what they do');
  change('B', 2097152, false);
  check(el('libSelBar').innerHTML.includes('<b>1</b>'), 'unchecking drops it');

  click({ 'data-lib-all': '1' });
  check(libUI.allMatching && el('libSelBar').innerHTML.includes('3 MB'), 'select all = everything matching');
  click({ 'data-lib-clear': '1' });
  check(!libUI.allMatching && libUI.selected.size === 0 && el('libSelBar').hidden, 'clear selection');

  // bulk, "delete the file": ONE confirmation with count, size and the posts that use them
  change('A', 1048576, true); change('B', 2097152, true);
  answers['/api/client/media-inventory/preview'] = { count: 2, size: 3145728, folders: [],
    affected_posts: [{ id: 7, title: 'פוסט קיץ', status: 'approved', planned_date: '2026-11-30' }] };
  answers['/api/client/media-inventory/delete'] = { done: 1, size: 1048576, trashed: 0,
    failed: [{ id: 'B', name: 'b.jpg', reason: 'owner_only' }] };
  click({ 'data-lib-bulk': 'file' }); await tick();
  const prev = calls.filter(c => c.url.endsWith('/preview')).pop();
  check(JSON.stringify(prev.body) === JSON.stringify({ mode: 'file', ids: ['A', 'B'] }), 'preview asks for the selection');
  const box = el('libPreview').innerHTML;
  check(!el('libPreview').hidden && box.includes(LIB_T.explainFile), 'the confirmation says what it does');
  check(box.includes('<b>2</b>') && box.includes('3 MB'), 'the confirmation: count and size');
  check(box.includes('פוסט קיץ') && box.includes(LIB_T.affected.replace('{n}', 1)), 'the usage notice');
  check((box.match(/data-lib-run=/g) || []).length === 1, 'one confirmation');
  check(!calls.some(c => c.url.endsWith('/delete')), 'nothing deleted before the confirmation');
  click({ 'data-lib-run': '1' }); await tick();
  const del = calls.filter(c => c.url.endsWith('/delete'));
  check(del.length === 1 && del[0].body.mode === 'file' && del[0].body.ids.join() === 'A,B', 'one delete, file mode');
  const res = el('libResult').innerHTML;
  check(res.includes(LIB_T.doneFile.replace('{n}', 1).replace('{size}', '1 MB')), 'how many went');
  check(res.includes('<li><bdi dir="auto">b.jpg</bdi> — ' + esc(LIB_T.reason_owner_only) + '</li>'),
        'the failed file with its plain reason');
  check(driveRefreshed > 0, 'the Drive list refreshes');

  // single item, "remove from the library"
  answers['/api/client/media-inventory/preview'] = { count: 1, size: 1048576, folders: [], affected_posts: [] };
  answers['/api/client/media-inventory/delete'] = { done: 1, size: 1048576, trashed: 0, failed: [] };
  click({ 'data-lib-one': 'index', 'data-id': 'A' }); await tick();
  const one = calls.filter(c => c.url.endsWith('/preview')).pop();
  check(JSON.stringify(one.body) === JSON.stringify({ mode: 'index', ids: ['A'] }), 'single item, index mode');
  check(el('libPreview').innerHTML.includes(LIB_T.explainIndex) && !el('libPreview').innerHTML.includes(LIB_T.affected.slice(0, 8)),
        'index mode explained; no notice when nothing uses it');
  click({ 'data-lib-run': '1' }); await tick();
  const last = calls.filter(c => c.url.endsWith('/delete')).pop();
  check(last.body.mode === 'index' && last.body.ids.join() === 'A', 'index delete sent');
  check(el('libResult').innerHTML.includes(LIB_T.doneIndex.replace('{n}', 1).replace('{size}', '1 MB')) &&
        !el('libResult').innerHTML.includes('<li>'), 'index result, no failures');
  console.log('OK');
})().catch(e => { console.error(e.message); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class Behaviour(unittest.TestCase):
    def test_selection_both_modes_the_notice_and_per_file_reasons(self):
        a = HTML.index("const libUI = {")
        block = HTML[a:HTML.index("// ═", HTML.index("// CONNECTED DRIVE FOLDERS") - 200)]
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
