"""Chat windows never move on their own (2026-11-25, dashboard/client/index.html).

What must stay true:
- clicking / focusing a window does not change the row's order (it used to
  bump flex `order`, so the clicked window jumped and the click was spent);
- a new message, minimise and restore do not change the order;
- a new window joins the END of the row and never shifts the others;
- one click on a window puts the caret in its input;
- a manual drag past a neighbour is the ONLY thing that reorders, and the row
  (order + minimised state) comes back the same after a refresh, per client.

The row code is run in Node against a small fake DOM: the real functions,
extracted from the page.

Run from the repo root:  python tests/test_chat_window_order.py -v
"""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from test_chat_uploads import _js_function   # noqa: E402

NODE = shutil.which("node")
HTML_PATH = os.path.join(ROOT, "dashboard", "client", "index.html")
ROW_FUNCTIONS = ("focusWindow", "chatRowKey", "applyChatRow", "saveChatRow", "readChatRow", "restoreChatRow",
                 "swapInChatRow", "wireWindowReorder", "wireWindowClickFocus", "minimizeWindow", "restoreWindow",
                 "closeAgentWindow", "openAgentWindow")

with open(HTML_PATH, encoding="utf-8") as f:
    HTML = f.read()

FAKE_DOM = r"""
function El(cls) {
  this.className = cls || ''; this.style = {}; this.listeners = {}; this.children = {};
  const set = new Set(); this.classList = {
    add: c => set.add(c), remove: c => set.delete(c), contains: c => set.has(c) };
  this._focused = false;
}
El.prototype.addEventListener = function (type, fn, opts) {
  (this.listeners[type] = this.listeners[type] || []).push({ fn: fn, capture: Boolean(opts && opts.capture) });
};
El.prototype.removeEventListener = function (type, fn) {
  this.listeners[type] = (this.listeners[type] || []).filter(l => l.fn !== fn);
};
El.prototype.fire = function (type, ev) {
  ev = Object.assign({ button: 0, pointerId: 1, stopped: false, target: { closest: () => null },
                       stopPropagation() { this.stopped = true; }, preventDefault() {} }, ev || {});
  const ls = (this.listeners[type] || []).slice().sort((a, b) => b.capture - a.capture);
  for (const l of ls) { if (ev.stopped) break; l.fn(ev); }
  return ev;
};
El.prototype.querySelector = function (sel) { return this.children[sel] || null; };
El.prototype.setPointerCapture = function () {};
El.prototype.remove = function () { this.removed = true; };
El.prototype.focus = function () { FOCUSED = this; };
El.prototype.getBoundingClientRect = function () {   // the row, left to right by flex order
  const left = Number(this.style.order || 0) * 312;
  return { left: left, width: 300 };
};
"""


def _page_code() -> str:
    return "\n".join(_js_function(HTML, n) for n in ROW_FUNCTIONS)


def node(script: str):
    harness = FAKE_DOM + r"""
let FOCUSED = null;
const STORE = {};
const localStorage = { getItem: k => (k in STORE ? STORE[k] : null), setItem: (k, v) => { STORE[k] = String(v); } };
const window = { getSelection: () => '' };
function page() {
  const ADMIN_VIEW = false;
  const TEAM_PERSONAS = { media: {}, meta: {}, website: {}, google: {} };
  const chatData = { clientId: 27 };
  const CHAT_WINDOWS = {}, VOICE_HANDLES = {};
  const launcher = new El('chat-launcher');
  const row = new El('chat-windows'); row.appendChild = el => { el.parent = row; };
  const document = { getElementById: id => (id === 'chatLauncher' ? launcher : row) };
  let CHAT_ROW = [];
  let chatWindowTop = 0;
  const REORDER_SLOP = 6;
  const voiceStop = () => {}, markThreadSeen = () => {}, renderLauncher = () => {}, renderChips = () => {};
  const autoGrowChatInput = () => {}, isPhoneChatLayout = () => false;
  const loadWindowHistory = () => Promise.resolve();
  function buildWindow(id) {
    const root = new El('chat-window');
    const head = new El('cw-head'); head.classList.add('cw-head');
    const stage = new El('cw-figure-stage');
    const input = new El('support-input');
    root.children['.cw-head'] = head; root.children['.cw-figure-stage'] = stage;
    root.addEventListener('mousedown', () => focusWindow(id));
    wireWindowClickFocus(id, root);
    wireWindowReorder(id, root);
    return { root: root, msgsEl: new El('cw-messages'), inputEl: input, chipsEl: null, minimized: false, head: head };
  }
  // a message arriving = what the page does: text into the window's message list
  function deliverMessage(id) { const w = CHAT_WINDOWS[id]; w.msgsEl.lastText = 'hi'; }
""" + _page_code() + r"""
  const order = () => CHAT_ROW.slice();
  const visual = () => Object.keys(CHAT_WINDOWS).sort((a, b) => CHAT_WINDOWS[a].root.style.order - CHAT_WINDOWS[b].root.style.order);
  const click = id => { const r = CHAT_WINDOWS[id].root; r.fire('mousedown'); r.fire('click'); };
  function drag(id, toX) {
    const w = CHAT_WINDOWS[id], rect = w.root.getBoundingClientRect(), x0 = rect.left + 150;
    w.head.fire('pointerdown', { clientX: x0, clientY: 10 });
    const steps = 12;
    for (let s = 1; s <= steps; s++) w.head.fire('pointermove', { clientX: x0 + (toX - x0) * s / steps, clientY: 10 });
    w.head.fire('pointerup', {});
  }
  return { CHAT_WINDOWS, order, visual, click, drag, openAgentWindow, minimizeWindow, restoreWindow,
           closeAgentWindow, deliverMessage, restoreChatRow, focused: () => FOCUSED };
}
"""
    out = subprocess.run([NODE, "-e", harness + script], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout.strip().splitlines()[-1])


OPEN3 = "const p = page(); ['general', 'media', 'meta'].forEach(id => p.openAgentWindow(id));\n"


@unittest.skipUnless(NODE, "node is not installed — the page code can't run here")
class NeverMovesOnItsOwn(unittest.TestCase):
    def test_click_does_not_reorder(self):
        got = node(OPEN3 + "p.click('meta'); p.click('general'); p.click('media');"
                           "console.log(JSON.stringify([p.order(), p.visual()]));")
        self.assertEqual(got, [["general", "media", "meta"]] * 2)

    def test_a_new_message_does_not_reorder(self):
        got = node(OPEN3 + "p.deliverMessage('meta'); p.openAgentWindow('meta');"   # flows re-open the window
                           "console.log(JSON.stringify([p.order(), p.visual()]));")
        self.assertEqual(got, [["general", "media", "meta"]] * 2)

    def test_minimise_and_restore_do_not_reorder(self):
        got = node(OPEN3 + "p.minimizeWindow('general'); p.minimizeWindow('media'); p.restoreWindow('general');"
                           "p.openAgentWindow('media');"
                           "console.log(JSON.stringify([p.order(), p.visual()]));")
        self.assertEqual(got, [["general", "media", "meta"]] * 2)

    def test_a_new_window_joins_the_end_and_shifts_nobody(self):
        got = node(OPEN3 + "const before = ['general','media','meta'].map(id => p.CHAT_WINDOWS[id].root.style.order);"
                           "p.openAgentWindow('website');"
                           "const after = ['general','media','meta'].map(id => p.CHAT_WINDOWS[id].root.style.order);"
                           "console.log(JSON.stringify([p.order(), before, after]));")
        self.assertEqual(got[0], ["general", "media", "meta", "website"])
        self.assertEqual(got[1], got[2])

    def test_one_click_focuses_the_input(self):
        got = node(OPEN3 + "p.click('media');"
                           "console.log(JSON.stringify([p.focused() === p.CHAT_WINDOWS.media.inputEl, p.order()]));")
        self.assertEqual(got, [True, ["general", "media", "meta"]])

    def test_a_click_on_a_control_is_left_to_the_control(self):
        got = node(OPEN3 + "p.CHAT_WINDOWS.general.inputEl.focus();"
                           "p.CHAT_WINDOWS.media.root.fire('click', { target: { closest: () => ({}) } });"
                           "console.log(JSON.stringify(p.focused() === p.CHAT_WINDOWS.general.inputEl));")
        self.assertTrue(got)

    def test_the_page_no_longer_bumps_order_on_focus(self):
        focus = _js_function(HTML, "focusWindow")
        self.assertNotIn(".order", focus)
        self.assertIn("style.zIndex", focus)
        # the only writer of the flex order is the row itself
        writers = [m.start() for m in re.finditer(r"style\.order\s*=", HTML)]
        self.assertEqual(len(writers), 1)
        self.assertIn("style.order = i", _js_function(HTML, "applyChatRow"))
        for name in ("appendMsg", "appendActionCard", "minimizeWindow", "restoreWindow", "focusWindow"):
            self.assertNotIn("CHAT_ROW", _js_function(HTML, name).replace("saveChatRow", ""), name)


@unittest.skipUnless(NODE, "node is not installed — the page code can't run here")
class ManualReorder(unittest.TestCase):
    def test_drag_past_a_neighbour_reorders(self):
        got = node(OPEN3 + "p.drag('meta', 10);"     # all the way to the left of the row
                           "console.log(JSON.stringify([p.order(), p.visual()]));")
        self.assertEqual(got, [["meta", "general", "media"]] * 2)

    def test_a_short_press_is_a_click_not_a_drag(self):
        got = node(OPEN3 + "const w = p.CHAT_WINDOWS.meta; w.head.fire('pointerdown', { clientX: 774, clientY: 10 });"
                           "w.head.fire('pointermove', { clientX: 777, clientY: 11 }); w.head.fire('pointerup', {});"
                           "console.log(JSON.stringify(p.order()));")
        self.assertEqual(got, ["general", "media", "meta"])

    def test_the_click_ending_a_drag_is_eaten(self):
        got = node(OPEN3 + "p.drag('meta', 10); const ev = p.CHAT_WINDOWS.meta.head.fire('click', {});"
                           "console.log(JSON.stringify(ev.stopped));")
        self.assertTrue(got)

    def test_order_and_state_survive_a_refresh(self):
        got = node(OPEN3 + "p.drag('general', 900); p.minimizeWindow('meta');"
                           "const saved = p.order();"
                           "FOCUSED = null; const q = page(); q.restoreChatRow();"   # the page loads again
                           "console.log(JSON.stringify([saved, q.order(), q.visual(),"
                           " q.CHAT_WINDOWS.meta.minimized, q.CHAT_WINDOWS.media.minimized, q.focused()]));")
        self.assertEqual(got[0], ["media", "meta", "general"])
        self.assertEqual(got[1], got[0])
        self.assertEqual(got[2], got[0])
        self.assertEqual(got[3:5], [True, False])
        self.assertIsNone(got[5])                                            # the refresh takes no focus

    def test_closing_leaves_the_others_in_place_and_is_remembered(self):
        got = node(OPEN3 + "p.closeAgentWindow('media'); const q = page(); q.restoreChatRow();"
                           "console.log(JSON.stringify([p.order(), q.order()]));")
        self.assertEqual(got, [["general", "meta"], ["general", "meta"]])

    def test_a_bad_saved_row_is_ignored(self):
        got = node("STORE['uallak_cw_row_27'] = JSON.stringify([{id: 'nobody'}, {id: 'media'}, 5]);"
                   "const q = page(); q.restoreChatRow(); console.log(JSON.stringify(q.order()));")
        self.assertEqual(got, ["media"])
        got = node("STORE['uallak_cw_row_27'] = '{not json';"
                   "const q = page(); q.restoreChatRow(); console.log(JSON.stringify(q.order()));")
        self.assertEqual(got, [])


if __name__ == "__main__":
    unittest.main()
