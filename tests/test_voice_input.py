"""Voice messages in the client chat (dashboard/assets/voice-input.js,
docs/VOICE-INPUT.md, docs/SYSTEM-REVIEW.md Part 4).

A. No audio ever reaches us: the script never opens the microphone itself,
   never records, never uploads; no new server route, key or env var exists.
B. Wiring: the button only where the browser can do it, never for staff,
   stops on send / minimise / close, every line in five languages.
C. Behaviour (the real script in Node with a fake recogniser): text lands in
   the box and is NOT sent; Cancel restores the box; a pause restarts quietly;
   refusals become calm lines; the length limit; one recording at a time;
   nothing is drawn where recognition is missing.

Run from the tests folder:  python -I -B test_voice_input.py -v
"""
import json
import os
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")
JS_PATH = os.path.join(ROOT, "dashboard", "assets", "voice-input.js")
KEYS = ("voice_start", "voice_cancel", "voice_done", "voice_listening", "voice_review", "voice_nothing",
        "voice_denied", "voice_no_mic", "voice_network", "voice_failed", "voice_limit")


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class NoAudioReachesUs(unittest.TestCase):
    def setUp(self):
        self.code = _src("dashboard", "assets", "voice-input.js").split("*/", 1)[1]

    def test_the_script_never_records_or_uploads(self):
        for banned in ("getUserMedia", "MediaRecorder", "AudioContext", "fetch(", "XMLHttpRequest", "FormData",
                       "WebSocket", "sendBeacon", "Blob(", "localStorage", "sessionStorage", "indexedDB"):
            self.assertNotIn(banned, self.code, banned)

    def test_no_new_server_side_transcription(self):
        api = _src("core", "api_server.py")
        for banned in ("transcri", "speech-to-text", "/voice-message", "whisper"):
            self.assertNotIn(banned, api.lower(), banned)
        keys = _src("agents", "keys_agent.py")
        for banned in ("OPENAI", "WHISPER", "DEEPGRAM", "ASSEMBLY", "SPEECH"):
            self.assertNotIn(banned, keys, banned)

    def test_privacy_policy_text_was_not_edited_but_the_todo_exists(self):
        self.assertNotIn("voice record", _src("dashboard", "privacy", "index.html").lower())
        self.assertIn("Privacy policy must mention voice recordings", _src("docs", "SYSTEM-REVIEW.md"))


class Wiring(unittest.TestCase):
    def setUp(self):
        self.page = _src("dashboard", "client", "index.html")

    def test_loaded_once_before_the_page_script_on_the_dashboard_only(self):
        tag = '<script src="/assets/voice-input.js"></script>'
        self.assertEqual(self.page.count(tag), 1)
        self.assertLess(self.page.index(tag), self.page.index("const DASH_I18N"))
        for other in ("admin", "landing", "login", "onboarding", "profile"):
            self.assertNotIn("voice-input", _src("dashboard", other, "index.html"), other)

    def test_only_where_supported_and_never_for_staff(self):
        self.assertIn("if (!ADMIN_VIEW && window.uallakVoice && uallakVoice.supported()) {", self.page)
        attach = self.page[self.page.index("VOICE_HANDLES[id] = uallakVoice.attach({"):][:400]
        self.assertIn("setText: text => setChatInput(input, text)", attach)
        self.assertNotIn("sendInWindow", attach)             # speech fills the box; it never sends

    def test_a_recording_stops_on_send_minimise_and_close(self):
        for fn in ("async function sendInWindow(", "function minimizeWindow(", "function closeAgentWindow("):
            body = self.page[self.page.index(fn):][:400]
            self.assertIn("voiceStop();", body, fn)

    def test_every_line_in_five_languages(self):
        for key in KEYS:
            line = next((ln for ln in self.page.splitlines() if ln.strip().startswith(key + ":")), "")
            self.assertTrue(line, key)
            for lang in ("he:", "en:", "fr:", "ar:", "ru:"):
                self.assertIn(lang, line, key)
            for vendor in ("Google", "Apple", "Microsoft", "Chrome", "Safari"):
                self.assertNotIn(vendor, line, key)          # no vendor names in front of a client

    def test_no_dom_move_after_build_and_reduced_motion(self):
        code = _src("dashboard", "assets", "voice-input.js").split("*/", 1)[1]
        attach = code[code.index("function attach(opts)"):code.index("function say(ctx, key)")]
        rest = code.replace(attach, "")
        for move in ("appendChild(", "insertBefore(", ".remove()", "removeChild("):
            self.assertNotIn(move, rest, move)
        self.assertIn("@media (prefers-reduced-motion: no-preference)", code)


HARNESS = r"""
const fs = require('fs'), vm = require('vm');
function el(tag) {
  const classes = new Set();
  const e = { tag, children: [], dataset: {}, style: {}, value: '', textContent: '', _l: {}, parentNode: null,
    classList: { add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c) },
    setAttribute() {}, focus() { e.focused = true; },
    addEventListener: (t, f) => { e._l[t] = f; },
    appendChild: c => { c.parentNode = e; e.children.push(c); return c; },
    insertBefore: (c, ref) => { c.parentNode = e; e.children.splice(e.children.indexOf(ref), 0, c); return c; },
    click: () => e._l.click && e._l.click() };
  Object.defineProperty(e, 'className', { get: () => [...classes].join(' '), set: v => { classes.clear(); String(v).split(' ').filter(Boolean).forEach(c => classes.add(c)); } });
  return e;
}
function page(opts) {
  opts = opts || {};
  const recs = [], timers = [], intervals = [];
  let now = 1000000;
  class SR {
    constructor() { recs.push(this); this.started = false; this.ended = false; }
    start() { if (opts.throwOnStart) throw new Error('nope'); this.started = true; }
    stop() { this.stopped = true; this.end(); }
    abort() { this.aborted = true; }
    result(parts) { this.onresult({ results: parts.map(p => { const r = [{ transcript: p[0] }]; r.isFinal = !!p[1]; return r; }) }); }
    error(code) { this.onerror({ error: code }); }
    end() { if (!this.ended) { this.ended = true; this.onend && this.onend(); } }
  }
  const doc = { hidden: false, _l: {}, head: el('head'), createElement: el,
                addEventListener: (t, f) => { doc._l[t] = f; } };
  const win = { document: doc, navigator: { userAgent: opts.ua || 'Mozilla/5.0 (Windows NT 10.0)' },
    setTimeout: (f, ms) => { timers.push([f, ms]); return timers.length; }, clearTimeout() {},
    setInterval: (f, ms) => { intervals.push(f); return intervals.length; }, clearInterval: id => { intervals[id - 1] = null; },
    Date: { now: () => now }, Math, JSON, console };
  if (!opts.unsupported) win.webkitSpeechRecognition = SR;
  win.window = win;
  vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), win);
  const make = () => {
    const area = el('div'), input = el('textarea'), send = el('button');
    area.appendChild(input); area.appendChild(send);
    const handle = win.uallakVoice.attach({ area, input, before: send, t: k => k, lang: () => opts.lang || 'he',
                                            setText: text => { input.value = text; } });
    const find = cls => area.children.find(c => c.classList.contains(cls));
    const strip = find('cw-rec');
    return { area, input, send, handle, mic: find('cw-mic'), note: find('cw-voice-note'), strip,
             cancel: strip && strip.children.find(c => c.dataset.voice === 'cancel'),
             done: strip && strip.children.find(c => c.dataset.voice === 'done'),
             time: strip && strip.children.find(c => c.classList.contains('cw-rec-time')) };
  };
  return { win, doc, recs, make, runTimers: () => { const t = timers.splice(0); t.forEach(x => x[0]()); },
           tick: seconds => { now += seconds * 1000; intervals.filter(Boolean).forEach(f => f()); } };
}
const out = {};
{ const p = page({ unsupported: true }); out.unsupported = [p.win.uallakVoice.supported(), p.make().mic === undefined]; }
{ // speak, Done: text in the box (after what was typed), not sent, cursor in the box
  const p = page(), w = p.make(); w.input.value = 'שלום';
  w.mic.click();
  out.recordingClass = w.area.classList.contains('recording');
  p.recs[0].result([['מה ', true], ['המצב', false]]);
  out.live = w.input.value;
  p.recs[0].result([['מה המצב ', true]]);
  w.done.click();
  out.afterDone = w.input.value; out.stripGone = !w.area.classList.contains('recording');
  out.note = w.note.textContent; out.focused = !!w.input.focused; out.lang = p.recs[0].lang;
  out.domOrder = w.area.children.map(c => c.className || c.tag); }
{ // Cancel restores the box exactly
  const p = page(), w = p.make(); w.input.value = 'טיוטה ';
  w.mic.click(); p.recs[0].result([['משהו אחר', true]]); out.beforeCancel = w.input.value;
  w.cancel.click(); out.afterCancel = w.input.value; out.aborted = !!p.recs[0].aborted;
  p.recs[0].result && (p.recs[0].onresult && 0); }
{ // a pause ends the session: restarted quietly, the text carries on
  const p = page(), w = p.make();
  w.mic.click(); p.recs[0].result([['חלק ראשון', true]]); p.recs[0].end();
  out.stillRecording = w.area.classList.contains('recording');
  p.runTimers(); out.sessions = p.recs.length;
  p.recs[1].result([['חלק שני', true]]); w.done.click(); out.joined = w.input.value; }
{ // microphone refused: a calm line, the button stays, nothing in the box
  const p = page(), w = p.make();
  w.mic.click(); p.recs[0].error('not-allowed'); p.recs[0].end();
  out.denied = [w.note.textContent, w.note.classList.contains('show'), w.area.classList.contains('recording'), w.input.value];
  p.runTimers(); out.deniedNoRestart = p.recs.length; }
{ const p = page(), w = p.make(); w.mic.click(); p.recs[0].error('no-speech'); p.recs[0].end(); p.runTimers();
  out.noSpeechRestarts = p.recs.length; w.done.click(); out.nothing = w.note.textContent; }
{ const p = page(), w = p.make(); w.mic.click(); p.recs[0].error('network'); p.recs[0].end(); out.network = w.note.textContent; }
{ const p = page({ throwOnStart: true }), w = p.make(); w.mic.click();
  out.startFailed = [w.note.textContent, w.area.classList.contains('recording')]; }
{ // the length limit
  const p = page(), w = p.make(); w.mic.click(); p.recs[0].result([['ארוך', true]]);
  p.tick(61); out.clock = w.time.textContent; p.tick(60);
  out.limit = [w.area.classList.contains('recording'), w.note.textContent, w.input.value, p.win.uallakVoice.MAX_SECONDS]; }
{ // one recording at a time; stop() keeps the text and ignores late words
  const p = page(), a = p.make(), b = p.make();
  a.mic.click(); p.recs[0].result([['ראשון', true]]); b.mic.click();
  out.onlyOne = [a.area.classList.contains('recording'), b.area.classList.contains('recording'), a.input.value];
  p.win.uallakVoice.stop(); out.stopped = b.area.classList.contains('recording');
  p.recs[1].onresult && p.recs[1].onresult({ results: [] }); }
{ // sent mid-recording: late words never land in the next message
  const p = page(), w = p.make(); w.mic.click(); const rec = p.recs[0]; rec.result([['נשלח', true]]);
  p.win.uallakVoice.stop(); w.input.value = '';
  rec.result([['מאוחר', true]]); rec.end(); out.late = w.input.value; }
{ const p = page(), w = p.make(); w.mic.click(); p.doc.hidden = true; p.doc._l.visibilitychange();
  out.hiddenStops = !w.area.classList.contains('recording'); }
{ const p = page({ lang: 'ar' }), w = p.make(); w.mic.click(); const first = p.recs[0].lang;
  p.recs[0].error('language-not-supported'); p.recs[0].end(); p.runTimers();
  out.arabic = [first, p.recs[1] && p.recs[1].lang, w.area.classList.contains('recording')]; }
{ const p = page({ ua: 'Mozilla/5.0 (Linux; Android 14)' }), w = p.make(); w.mic.click(); out.androidContinuous = p.recs[0].continuous; }
{ const p = page(), w = p.make(); w.mic.click(); out.desktopContinuous = p.recs[0].continuous;
  out.helpers = [p.win.uallakVoice._clock(75), p.win.uallakVoice._join('a', ' b  c '), p.win.uallakVoice._join('', 'x'),
                 p.win.uallakVoice._errorKey('service-not-allowed'), p.win.uallakVoice._errorKey('aborted')]; }
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed — the browser script can't run here")
class Behaviour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = subprocess.run([NODE, "-e", HARNESS, JS_PATH], capture_output=True, text=True, encoding="utf-8",
                             timeout=60)
        if run.returncode != 0:
            raise AssertionError(run.stderr[-3000:])
        cls.out = json.loads(run.stdout.strip().splitlines()[-1])

    def test_no_button_where_the_browser_cannot_do_it(self):
        self.assertEqual(self.out["unsupported"], [False, True])

    def test_spoken_words_land_in_the_box_for_review(self):
        o = self.out
        self.assertTrue(o["recordingClass"])
        self.assertEqual(o["live"], "שלום מה המצב")
        self.assertEqual(o["afterDone"], "שלום מה המצב")
        self.assertTrue(o["stripGone"])
        self.assertEqual(o["note"], "voice_review")
        self.assertTrue(o["focused"])
        self.assertEqual(o["lang"], "he-IL")
        # built once, in this order: [box] [mic] [send] [recording strip] [note — showing the review line]
        self.assertEqual(o["domOrder"], ["textarea", "cw-mic", "button", "cw-rec", "cw-voice-note show"])

    def test_cancel_restores_the_box(self):
        self.assertEqual(self.out["beforeCancel"], "טיוטה משהו אחר")
        self.assertEqual(self.out["afterCancel"], "טיוטה ")
        self.assertTrue(self.out["aborted"])

    def test_a_pause_restarts_quietly_and_the_text_carries_on(self):
        self.assertTrue(self.out["stillRecording"])
        self.assertEqual(self.out["sessions"], 2)
        self.assertEqual(self.out["joined"], "חלק ראשון חלק שני")

    def test_refusals_are_calm_lines(self):
        self.assertEqual(self.out["denied"], ["voice_denied", True, False, ""])
        self.assertEqual(self.out["deniedNoRestart"], 1)                 # no retry loop against a refusal
        self.assertEqual(self.out["noSpeechRestarts"], 2)                # silence is not an error
        self.assertEqual(self.out["nothing"], "voice_nothing")
        self.assertEqual(self.out["network"], "voice_network")
        self.assertEqual(self.out["startFailed"], ["voice_failed", False])

    def test_length_limit(self):
        self.assertEqual(self.out["clock"], "1:01")
        self.assertEqual(self.out["limit"], [False, "voice_limit", "ארוך", 120])

    def test_one_recording_at_a_time_and_stop(self):
        self.assertEqual(self.out["onlyOne"], [False, True, "ראשון"])
        self.assertFalse(self.out["stopped"])
        self.assertEqual(self.out["late"], "")
        self.assertTrue(self.out["hiddenStops"])

    def test_languages_and_platform_quirks(self):
        self.assertEqual(self.out["arabic"], ["ar-IL", "ar-SA", True])
        self.assertFalse(self.out["androidContinuous"])
        self.assertTrue(self.out["desktopContinuous"])
        self.assertEqual(self.out["helpers"], ["1:15", "a b c", "x", "voice_denied", ""])


if __name__ == "__main__":
    unittest.main()
