"""The Social tab's enlarged image view (2026-11-24, /assets/media-viewer.js).

What must stay true:
- clicking an image in the Social tab (a queue item's picture, a design's
  slide) opens it enlarged in an overlay;
- inside, the user moves through the SAME context — the slides of that
  carousel (queue items sharing post_ref.set_id, in slide order) or the
  slides of that design — with the arrows, the keyboard (visual order on an
  RTL page: ← is next), a swipe, and a "3 / 5" counter;
- ✕, a click on the dark area and Esc close it;
- the image fits the window, uncropped, never above its natural size;
- the card keeps its thumbnail; the full-size image loads only in the overlay,
  and only the next and previous one are preloaded;
- nothing else in the Social tab changes.

Runs the dashboard's own code (the viewer + the Social tab's wiring) under
Node with a small fake DOM; skipped when Node is not installed.
Run from the tests folder:  python test_social_lightbox.py -v
"""
import os
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
VIEWER = _read("dashboard", "assets", "media-viewer.js")


def _fn(name):
    a = HTML.index(name)
    return HTML[a:HTML.index("\n}\n", a) + 3]


class Static(unittest.TestCase):
    def test_images_are_clickable_and_the_grid_keeps_its_thumbnails(self):
        row = _fn("function socialRow(item)")
        self.assertIn("const thumb = '/api/client/media-drive/thumb/' + encodeURIComponent(item.file_id);", row)
        self.assertIn('<img src="${esc(thumb)}" alt="" loading="lazy"', row)
        self.assertIn("${item.kind === 'image'\n        ? ` data-soc-view=\"${esc(item.id)}\"", row)
        self.assertIn('data-soc-slide="${esc(item.id)}" data-n="${i}"', _fn("function socialDesignRow(item)"))
        wire = _fn("function wireSocial(list)")
        self.assertIn("on('data-soc-view', id => socialOpenViewer(id));", wire)
        self.assertIn("on('data-soc-slide', (id, img) => socialOpenDesign(id, img.getAttribute('data-n')));", wire)

    def test_fits_without_cropping_or_upscaling(self):
        self.assertIn(".mv-stage img{max-width:100%;max-height:100%;object-fit:contain;", VIEWER)
        self.assertNotIn("mv-stage img{width:100%", VIEWER)

    def test_nothing_else_in_the_social_tab_changed(self):
        wire = _fn("function wireSocial(list)")
        for handler in ("data-soc-sel", "data-soc-play", "data-soc-approve", "data-soc-edit", "data-soc-reject",
                        "data-soc-cancel", "data-soc-save", "data-soc-copysave", "data-soc-critique-send",
                        "data-soc-reject-send", "data-soc-published", "data-soc-copy", "data-soc-dl"):
            self.assertIn(handler, wire)
        row = _fn("function socialRow(item)")
        for button in ('data-soc-approve="${esc(item.id)}">${esc(t(\'soc_approve\'))}',
                       'data-soc-edit="${esc(item.id)}">${esc(t(\'soc_edit\'))}',
                       'data-soc-reject="${esc(item.id)}">${esc(t(\'soc_reject\'))}',
                       'data-soc-dl="${esc(item.id)}">${esc(t(\'soc_download\'))}',
                       'data-soc-published="${esc(item.id)}">${esc(t(\'soc_published_btn\'))}'):
            self.assertIn(button, row)


HARNESS = r"""
const vm = require('vm');
const fs = require('fs');
const [viewerSrc, glueSrc, wireSrc] = [2, 3, 4].map(i => fs.readFileSync(process.argv[i], 'utf8'));
function check(c, m) { if (!c) throw new Error(m); }

// ── a small DOM ──
class N {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null; this.attrs = {};
    this.style = {}; this.dataset = {}; this._cls = new Set(); this.listeners = {}; this._text = ''; this.src = ''; this.id = ''; }
  get className() { return [...this._cls].join(' '); }
  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }
  get classList() { const s = this._cls; return { add: c => s.add(c), remove: c => s.delete(c), contains: c => s.has(c),
    toggle: (c, on) => ((on === undefined ? !s.has(c) : on) ? s.add(c) : s.delete(c)) }; }
  set textContent(v) { this.children.forEach(c => { c.parentNode = null; }); this.children = []; this._text = String(v); }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  appendChild(c) { if (c.parentNode) c.parentNode.removeChild(c); this.children.push(c); c.parentNode = this; return c; }
  removeChild(c) { this.children = this.children.filter(x => x !== c); c.parentNode = null; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  fire(t, ev) { ev = Object.assign({ target: this, stopPropagation() {}, preventDefault() {} }, ev); (this.listeners[t] || []).forEach(f => f(ev)); }
  focus() { document.activeElement = this; }
  querySelectorAll() { return []; }
  all() { return [this].concat(...this.children.map(c => c.all())); }
}
const docKeys = new Set();
const head = new N('head'), body = new N('body');
globalThis.document = { head, body, documentElement: { dir: 'rtl' }, activeElement: null,
  createElement: t => new N(t), getElementById: id => head.all().concat(body.all()).find(n => n.id === id) || null,
  addEventListener: (t, f) => { if (t === 'keydown') docKeys.add(f); }, removeEventListener: (t, f) => { if (t === 'keydown') docKeys.delete(f); } };
globalThis.window = globalThis;
const preloaded = [];
globalThis.Image = class { set src(v) { preloaded.push(v); } };
const key = k => [...docKeys].forEach(f => f({ key: k, preventDefault() {}, stopPropagation() {}, shiftKey: false }));

vm.runInThisContext(viewerSrc);

// ── the Social tab around the glue ──
const HE = { mv_close: 'סגירה', mv_prev: 'הקודם', mv_next: 'הבא', mv_preview_failed: 'לא הצלחנו', mv_view: 'תצוגה מוגדלת' };
globalThis.uallakI18n = { t: k => HE[k] || k };
globalThis.mediaViewerOpts = () => ({ t: (k) => HE[k] || k, maxMb: 0, notify() {}, fileUrl: id => '/api/client/media-drive/file/' + id });
const img = (id, set, n, kind) => ({ id, kind: kind || 'image', file_id: 'FILE_' + id + '_abcdef', title: 'שקופית ' + id,
  channels: ['instagram'], status: 'waiting', ...(set ? { post_ref: { set_id: set, n, of: 5 } } : {}) });
globalThis.social = { items: [img('c', 'S1', 3), img('a', 'S1', 1), img('e', 'S1', 5), img('x', 'S2', 1), img('b', 'S1', 2),
  img('v', 'S1', 6, 'video'), img('d', 'S1', 4), img('solo'),
  { id: 'des', kind: 'design', title: 'עיצוב', status: 'waiting', design: { previews: ['/api/client/social/des/slide/1?rev=2',
    '/api/client/social/des/slide/2?rev=2', '/api/client/social/des/slide/3?rev=2'] } }] };
globalThis.socialItem = id => social.items.find(i => i.id === id) || null;
globalThis.socialChannelName = c => c;
vm.runInThisContext(glueSrc + '\n' + wireSrc);

// the list's clickable images, as wireSocial finds them
const listImgs = social.items.flatMap(i => i.kind === 'design'
  ? i.design.previews.map((_, n) => { const e = new N('img'); e.setAttribute('data-soc-slide', i.id); e.setAttribute('data-n', String(n)); return e; })
  : i.kind === 'image' ? [(() => { const e = new N('img'); e.setAttribute('data-soc-view', i.id); return e; })()] : []);
const fakeList = { querySelectorAll: sel => listImgs.filter(e => sel.split(',').some(s => {
  const a = s.trim().replace(/^\[|\]$/g, ''); return e.getAttribute(a) !== null; })) };
wireSocial(fakeList);

const overlay = () => body.children.find(c => c.className.includes('mv-overlay'));
const isOpen = () => Boolean(overlay() && overlay().classList.contains('open'));
const shown = () => { const s = overlay().all().find(n => n.className === 'mv-stage'); const im = s.children.find(c => c.tagName === 'IMG'); return im ? im.src : ''; };
const counter = () => overlay().all().find(n => n.className === 'mv-count').textContent;
const file = id => '/api/client/media-drive/file/FILE_' + id + '_abcdef?inline=1';
const btn = cls => overlay().all().find(n => n.className === 'mv-btn ' + cls);

(async () => {
  // opens on click — the carousel's 3rd slide
  check(!overlay(), 'no overlay before a click');
  listImgs.find(e => e.getAttribute('data-soc-view') === 'c').onclick();
  check(isOpen(), 'the click opens the overlay');
  check(counter() === '3 / 5', 'counter 3 / 5 (the video and the other set are not in it), got ' + counter());
  check(shown() === file('c'), 'full size, loaded now: ' + shown());
  check(preloaded.length === 2 && preloaded.includes(file('d')) && preloaded.includes(file('b')), 'only next + previous preloaded: ' + preloaded);

  // RTL keyboard: ← is next, → is previous
  key('ArrowLeft');
  check(counter() === '4 / 5' && shown() === file('d'), 'ArrowLeft = next on RTL');
  key('ArrowRight'); key('ArrowRight');
  check(counter() === '2 / 5' && shown() === file('b'), 'ArrowRight = previous on RTL');
  // the arrows; next sits at the inline end (left on RTL)
  check(btn('mv-nav next').textContent === '‹' && btn('mv-nav prev').textContent === '›', 'RTL arrow glyphs');
  btn('mv-nav next').onclick({ stopPropagation() {} });
  check(counter() === '3 / 5', 'next button');
  btn('mv-nav prev').onclick({ stopPropagation() {} }); btn('mv-nav prev').onclick({ stopPropagation() {} });
  check(counter() === '1 / 5' && shown() === file('a'), 'previous button, in slide order');
  btn('mv-nav prev').onclick({ stopPropagation() {} });
  check(counter() === '5 / 5' && shown() === file('e'), 'wraps around');
  // swipe: on RTL a swipe to the right brings the next one
  overlay().fire('touchstart', { touches: [{ clientX: 100 }] }); overlay().fire('touchend', { changedTouches: [{ clientX: 300 }] });
  check(counter() === '1 / 5', 'swipe right = next on RTL');
  overlay().fire('touchstart', { touches: [{ clientX: 300 }] }); overlay().fire('touchend', { changedTouches: [{ clientX: 100 }] });
  check(counter() === '5 / 5', 'swipe left = previous on RTL');
  overlay().fire('touchstart', { touches: [{ clientX: 300 }] }); overlay().fire('touchend', { changedTouches: [{ clientX: 290 }] });
  check(counter() === '5 / 5', 'a tiny move is not a swipe');

  // close: ✕
  btn('mv-close').onclick();
  check(!isOpen(), '✕ closes');
  // close: the dark area (a click on the image itself does not close)
  listImgs.find(e => e.getAttribute('data-soc-view') === 'a').onclick();
  check(isOpen() && counter() === '1 / 5', 'reopens on the clicked slide');
  const stageImg = overlay().all().find(n => n.tagName === 'IMG');
  overlay().fire('click', { target: stageImg });
  check(isOpen(), 'a click on the image keeps it open');
  overlay().fire('click', { target: overlay() });
  check(!isOpen(), 'a click on the dark area closes');
  // close: Esc
  listImgs.find(e => e.getAttribute('data-soc-view') === 'x').onclick();
  check(isOpen() && counter() === '' && shown() === file('x'), 'a set of one: no counter');
  key('Escape');
  check(!isOpen() && docKeys.size === 0, 'Esc closes and lets the keys go');

  // a standalone image: just itself
  listImgs.find(e => e.getAttribute('data-soc-view') === 'solo').onclick();
  check(isOpen() && counter() === '' && shown() === file('solo'), 'a standalone image');
  key('ArrowLeft');
  check(shown() === file('solo'), 'nothing to move to');
  key('Escape');

  // a design's slides
  preloaded.length = 0;
  listImgs.filter(e => e.getAttribute('data-soc-slide') === 'des')[1].onclick();
  check(isOpen() && counter() === '2 / 3' && shown() === '/api/client/social/des/slide/2?rev=2', 'the design slide 2 / 3');
  check(preloaded.length === 2 && preloaded.includes('/api/client/social/des/slide/3?rev=2')
        && preloaded.includes('/api/client/social/des/slide/1?rev=2'), 'design: next + previous preloaded');
  key('ArrowLeft');
  check(counter() === '3 / 3', 'design: next');
  key('Escape');

  // a failed full-size load falls back to the thumbnail once
  listImgs.find(e => e.getAttribute('data-soc-view') === 'solo').onclick();
  const im = overlay().all().find(n => n.tagName === 'IMG');
  im.onerror();
  check(im.src === '/api/client/media-drive/thumb/FILE_solo_abcdef', 'falls back to the thumbnail');
  key('Escape');

  // the Enter key on a focused image opens it too
  const e = listImgs.find(x => x.getAttribute('data-soc-view') === 'b');
  e.click = () => e.onclick();
  e.onkeydown({ key: 'Enter', preventDefault() {} });
  check(isOpen() && counter() === '2 / 5', 'Enter opens');
  key('Escape');
  console.log('OK');
})().catch(e => { console.error(e.message); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class Behaviour(unittest.TestCase):
    def test_open_browse_count_close_and_rtl_keys(self):
        a = HTML.index("// ─── Enlarged view (lightbox")
        glue = HTML[a:HTML.index("function renderSocialSelBar() {", a)]
        wire = _fn("function wireSocial(list)")
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name, text in (("viewer.js", VIEWER), ("glue.js", glue), ("wire.js", wire), ("harness.js", HARNESS)):
                path = os.path.join(tmp, name)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                paths.append(path)
            run = subprocess.run(["node", paths[3], paths[0], paths[1], paths[2]], capture_output=True, text=True,
                                 encoding="utf-8", timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        self.assertIn("OK", run.stdout)


if __name__ == "__main__":
    unittest.main()
