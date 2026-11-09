"""Page-driven warm-up (core/warmup, dashboard/assets/warmup.js, docs/WARMUP.md).

Server: the ping is in-memory only (no Supabase / Drive / model), rate-limited
per IP, reports the Cloud Run revision; the interval is one admin setting.
Page: warmup.js runs in Node with a fake document / fetch / timers: ping on
load, every interval while visible, stop when hidden, ping on return, reload
on a new version only when idle, once per version, silently.

Stdlib only; the Node part is skipped (with a reason) when node is missing.
Run from the repo root:  python tests/test_warmup.py -v
"""
import json
import os
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import warmup   # noqa: E402

NODE = shutil.which("node")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _function(name):
    src = _read("core", "api_server.py")
    start = src.index(f"def {name}(")
    start = src.rindex("\n@app.", 0, start) + 1
    end = src.index("\n@app.", start + 5)
    return src[start:end]


class Server(unittest.TestCase):
    def setUp(self):
        warmup._hits.clear()

    def test_version_is_the_cloud_run_revision(self):
        saved = os.environ.get("K_REVISION")
        try:
            os.environ["K_REVISION"] = "super-system-00321-abc"
            self.assertEqual(warmup.ping(), {"ok": True, "version": "super-system-00321-abc"})
            del os.environ["K_REVISION"]
            self.assertEqual(warmup.ping()["version"], "dev")
        finally:
            if saved is not None:
                os.environ["K_REVISION"] = saved

    def test_rate_limit_per_ip_per_minute(self):
        for i in range(warmup.RATE_MAX):
            self.assertTrue(warmup.allow("1.2.3.4", now=100.0 + i * 0.01))
        self.assertFalse(warmup.allow("1.2.3.4", now=101.0))
        self.assertTrue(warmup.allow("5.6.7.8", now=101.0))                 # another visitor
        self.assertTrue(warmup.allow("1.2.3.4", now=100.0 + warmup.RATE_WINDOW_S + 1))   # window passed

    def test_memory_stays_bounded(self):
        for i in range(warmup._TRACK_MAX + 10):
            warmup.allow(f"10.0.{i // 256}.{i % 256}", now=1.0)
        self.assertLessEqual(len(warmup._hits), warmup._TRACK_MAX + 1)

    def test_ping_touches_nothing(self):
        src = _read("core", "warmup.py")
        ping_src = src[src.index("def version"):src.index("def interval_seconds")]
        for word in ("_db", "supabase", "drive", "claude", "anthropic", "admin_service", "requests", "httpx"):
            self.assertNotIn(word, ping_src.lower())
        route = _function("warmup_ping")
        self.assertIn('@app.get("/api/warmup/")', route)
        self.assertIn("async def warmup_ping", route)
        self.assertIn("warmup.allow(", route)
        self.assertIn("429", route)
        self.assertIn("no-store", _read("core", "api_server.py")[_read("core", "api_server.py").index("_WARMUP_HEADERS"):][:80])
        for word in ("_db(", "_require_session", "get_setting", "client_state"):
            self.assertNotIn(word, route)

    def test_routes_before_the_root_mount(self):
        src = _read("core", "api_server.py")
        self.assertLess(src.index('@app.get("/api/warmup/")'), src.index('app.mount("/", StaticFiles'))

    def test_interval_is_one_admin_setting_clamped(self):
        from core import admin_service
        self.assertEqual(admin_service.DEFAULT_SETTINGS["warmup_interval_seconds"], 240)
        saved = admin_service.get_setting
        try:
            for stored, expected in ((240, 240), (10, 60), (99999, 3600), ("bad", 240), (None, 240)):
                admin_service.get_setting = lambda key, default=None, ttl=60, v=stored: v
                self.assertEqual(warmup.interval_seconds(), expected, stored)
        finally:
            admin_service.get_setting = saved
        self.assertIn("set-warmup", _read("dashboard", "admin", "index.html"))
        self.assertIn("warmup_interval_seconds:", _read("dashboard", "admin", "index.html"))

    def test_no_always_on_and_no_scheduler_job(self):
        self.assertNotIn("warmup", _read("scripts", "schedule_jobs.sh"))
        for path in (("core", "warmup.py"), ("dashboard", "assets", "warmup.js"), ("docs", "WARMUP.md")):
            self.assertNotRegex(_read(*path), r"gcloud run [^\n]*(--no-cpu-throttling|--min-instances)")

    def test_pages_load_it(self):
        self.assertIn('<script src="/assets/warmup.js" async></script>', _read("dashboard", "client", "index.html"))
        for page in ("index.html", "operator/index.html", "landing/index.html", "connections/index.html",
                     "models/index.html"):
            self.assertIn("/assets/warmup.js", _read("dashboard", "admin", *page.split("/")), page)
        dash = _read("dashboard", "client", "index.html")
        for fn in ("uploadFilesInWindow", "uploadVoiceFile", "sendInWindow"):
            self.assertIn(f"{fn} = busyWhile({fn});", dash)


HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const code = fs.readFileSync(process.argv[1], 'utf8');
function make(versions, opts) {
  opts = opts || {};
  const log = { pings: 0, configs: 0, reloads: 0, errors: [], debug: 0 };
  let now = 0; const timers = []; let id = 0;
  const listeners = {};
  const store = {};
  const doc = { visibilityState: 'visible', activeElement: null, areas: [],
    addEventListener: (ev, fn) => (listeners[ev] = listeners[ev] || []).push(fn),
    querySelectorAll: () => doc.areas };
  const win = { addEventListener: (ev, fn) => (listeners['w:' + ev] = listeners['w:' + ev] || []).push(fn),
    location: { reload: () => { log.reloads++; } }, __uallakBusy: 0 };
  const ctx = {
    window: win, document: doc,
    sessionStorage: { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); } },
    console: { debug: () => { log.debug++; }, log: () => log.errors.push('log'), error: () => log.errors.push('error'),
               warn: () => log.errors.push('warn') },
    setInterval: (fn, ms) => { timers.push({ id: ++id, fn, ms, at: now + ms, every: true }); return id; },
    clearInterval: tid => { const i = timers.findIndex(t => t.id === tid); if (i >= 0) timers.splice(i, 1); },
    setTimeout: (fn, ms) => { timers.push({ id: ++id, fn, ms, at: now + ms, every: false }); return id; },
    fetch: async (url) => {
      if (url === '/api/warmup/config') { log.configs++;
        return { ok: true, json: async () => ({ ok: true, interval_seconds: opts.interval || 240, version: versions[0] }) }; }
      log.pings++;
      if (opts.fail) throw new Error('offline');
      const v = versions[Math.min(log.pings - 1, versions.length - 1)];
      return { ok: true, json: async () => ({ ok: true, version: v }) };
    },
  };
  ctx.window.UallakWarmup = undefined;
  vm.createContext(ctx); vm.runInContext(code, ctx);
  const flush = () => new Promise(r => setImmediate(r));
  return {
    log, doc, win, store,
    async advance(ms) {
      const end = now + ms;
      for (;;) {
        timers.sort((a, b) => a.at - b.at);
        const t = timers[0];
        if (!t || t.at > end) break;
        now = t.at;
        if (t.every) t.at += t.ms; else timers.shift();
        t.fn(); await flush(); await flush();
      }
      now = end; await flush(); await flush();
    },
    async hide() { doc.visibilityState = 'hidden'; (listeners.visibilitychange || []).forEach(f => f()); await flush(); },
    async show() { doc.visibilityState = 'visible'; (listeners.visibilitychange || []).forEach(f => f()); await flush(); await flush(); await flush(); },
    running: () => timers.filter(t => t.every).length,
    flush,
  };
}
(async () => {
  const out = {};
  // a. on load + b. every 4 minutes while visible, stopped while hidden
  let p = make(['v1']); await p.flush(); await p.flush();
  out.load_pings = p.log.pings; out.config = p.log.configs;
  await p.advance(240000 * 3); out.after_12_min = p.log.pings;
  await p.hide(); const atHide = p.log.pings; out.running_hidden = p.running();
  await p.advance(240000 * 5); out.hidden_pings = p.log.pings - atHide;
  // c. back visible: pings at once; same version → no reload
  await p.show(); out.return_ping = p.log.pings - atHide; out.running_again = p.running(); out.reload_same = p.log.reloads;
  // the interval comes from the setting
  let q = make(['v1'], { interval: 60 }); await q.flush(); await q.flush(); await q.advance(60000 * 3);
  out.interval_60 = q.log.pings;
  // new version on return, idle → one reload, once per version
  let r = make(['v1', 'v2']); await r.flush(); await r.flush();   // load ping v1, return ping v2
  await r.hide(); await r.show(); await r.advance(10); out.idle_reload = r.log.reloads;
  out.reloaded_for = r.store['uallak_warmup_reloaded_for'];
  let r2 = make(['v1', 'v2']); r2.store['uallak_warmup_reloaded_for'] = 'v2'; await r2.flush(); await r2.flush();
  await r2.hide(); await r2.show(); await r2.advance(10000); out.no_loop = r2.log.reloads;
  // busy: draft / typing / upload → waits, then reloads when idle
  let b = make(['v1', 'v2']); await b.flush(); await b.flush();
  b.doc.areas = [{ value: 'half a message' }];
  await b.hide(); await b.show(); await b.advance(20000); out.draft_reloads = b.log.reloads;
  b.doc.areas = []; b.doc.activeElement = { tagName: 'TEXTAREA' }; await b.advance(20000); out.typing_reloads = b.log.reloads;
  b.doc.activeElement = null; b.win.__uallakBusy = 1; await b.advance(20000); out.upload_reloads = b.log.reloads;
  b.win.__uallakBusy = 0; await b.advance(3000); out.idle_after_busy = b.log.reloads;
  // silent failures
  let f = make(['v1'], { fail: true }); await f.flush(); await f.flush(); await f.advance(240000 * 2);
  out.fail_pings = f.log.pings; out.fail_noise = f.log.errors.length; out.fail_reloads = f.log.reloads;
  console.log(JSON.stringify(out));
})().catch(e => { console.log(JSON.stringify({ crash: String(e && e.stack || e) })); });
"""


@unittest.skipUnless(NODE, "node is not installed — the page script can't run here")
class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = subprocess.run([NODE, "-e", HARNESS, os.path.join(ROOT, "dashboard", "assets", "warmup.js")],
                             capture_output=True, text=True, timeout=60)
        cls.out = json.loads(run.stdout.strip().splitlines()[-1])

    def test_no_crash(self):
        self.assertNotIn("crash", self.out, self.out.get("crash"))

    def test_pings_on_load_and_every_interval_while_visible(self):
        self.assertEqual(self.out["load_pings"], 1)
        self.assertEqual(self.out["config"], 1)
        self.assertEqual(self.out["after_12_min"], 4)          # load + 3 × 4 min
        self.assertEqual(self.out["interval_60"], 4)           # the admin setting drives it

    def test_hidden_tab_stops_and_return_pings_at_once(self):
        self.assertEqual(self.out["running_hidden"], 0)
        self.assertEqual(self.out["hidden_pings"], 0)
        self.assertEqual(self.out["return_ping"], 1)
        self.assertEqual(self.out["running_again"], 1)
        self.assertEqual(self.out["reload_same"], 0)

    def test_new_version_reloads_once_when_idle(self):
        self.assertEqual(self.out["idle_reload"], 1)
        self.assertEqual(self.out["reloaded_for"], "v2")
        self.assertEqual(self.out["no_loop"], 0)

    def test_never_reloads_while_drafting_typing_or_uploading(self):
        self.assertEqual(self.out["draft_reloads"], 0)
        self.assertEqual(self.out["typing_reloads"], 0)
        self.assertEqual(self.out["upload_reloads"], 0)
        self.assertEqual(self.out["idle_after_busy"], 1)

    def test_failures_are_silent(self):
        self.assertEqual(self.out["fail_pings"], 3)            # tried again each interval
        self.assertEqual(self.out["fail_noise"], 0)            # console.debug only
        self.assertEqual(self.out["fail_reloads"], 0)


if __name__ == "__main__":
    unittest.main()
