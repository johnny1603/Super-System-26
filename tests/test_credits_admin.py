"""Control Room → client → credits for the Drive asset index (2026-11-24,
core/drive_assets.credits_view + load_credits, dashboard/admin).

What must stay true:
- the balance shows: free allowance + loaded credits − used;
- an open quote (indexing stopped at the cap) shows its folders, the items
  left, the MEASURED cost per item and the total;
- the measured cost per item of the LAST run is shown, so the quote is
  explainable;
- loading credits (amount + optional note, who loaded it) updates the
  balance and the history, and indexing resumes where it stopped — the
  screen shows the job resumed and how many items remain;
- staff only: both routes require the admin session; the client dashboard
  never calls them.

Run from the repo root:  python tests/test_credits_admin.py -v
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_drive_folders as tdf  # noqa: E402  (the in-memory Drive / model / database)

da = tdf.da
CID = tdf.CID
ROOT = tdf.ROOT


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Credits(tdf.Base):
    def setUp(self):
        super().setUp()
        self.cfg["free_items"] = 10
        self.drive.add("BIG_FOLDER_00001", "מוצרים")
        for n in range(25):
            tdf.media(self.drive, f"CAP_{n:02d}_abcdef", f"IMG_{n:04d}.jpg", "BIG_FOLDER_00001",
                      modified=f"2026-10-{n + 1:02d}T00:00:00Z")
        self.root = self.connect("BIG_FOLDER_00001", "product_catalog")

    def test_balance_and_open_quote_numbers(self):
        self.run_all()
        v = da.credits_view(CID)
        self.assertEqual(v["balance"], {"free_items": 10, "extra_items": 0, "allowance": 10, "used": 10,
                                        "remaining": 0})
        q = v["quote"]
        self.assertEqual(q["remaining"], 15)
        self.assertAlmostEqual(q["per_item_usd"], self.usd_per_image, places=6)
        self.assertAlmostEqual(q["quote_usd"], 15 * self.usd_per_image, places=6)
        self.assertTrue(q["folders"] and "מוצרים" in q["folders"][0])
        # the last run's measured cost per item
        self.assertEqual(v["last_run"]["items"], 10)
        self.assertAlmostEqual(v["last_run"]["per_item_usd"], self.usd_per_image, places=6)
        self.assertGreater(v["last_run"]["per_item_ils"], 0)
        self.assertEqual(v["pending"], 15)
        self.assertEqual(v["history"], [])

    def test_loading_updates_balance_history_and_resumes(self):
        self.run_all()
        da.load_credits(CID, 5, note="חשבונית 1042", by="admin:דנה")
        v = da.credits_view(CID)
        self.assertEqual(v["balance"]["extra_items"], 5)
        self.assertEqual(v["balance"]["allowance"], 15)
        self.assertEqual(v["balance"]["remaining"], 5)
        self.assertEqual(v["quote"], {})                         # the cap is cleared
        self.assertEqual(v["job"]["state"], "queued")            # resumed, shown as such
        self.assertEqual(v["job"]["limit"], 5)
        self.assertEqual(v["pending"], 15)
        h = v["history"][0]
        self.assertEqual((h["items"], h["note"], h["by"]), (5, "חשבונית 1042", "admin:דנה"))
        self.assertTrue(h["at"])
        # indexing resumes from where it stopped
        before = {k for k, i in self.items().items() if i["state"] == "indexed"}
        self.run_all()
        v = da.credits_view(CID)
        self.assertEqual(v["balance"]["used"], 15)
        self.assertEqual(v["pending"], 10)
        self.assertEqual(v["last_run"]["items"], 5)              # this run, not the total
        after = {k for k, i in self.items().items() if i["state"] == "indexed"}
        self.assertTrue(before < after and len(after) == 15)     # continued, nothing re-described
        # a second load lists newest first
        da.load_credits(CID, 3, note="", by="admin")
        self.assertEqual([x["items"] for x in da.credits_view(CID)["history"]], [3, 5])


class StaffOnly(unittest.TestCase):
    def _route(self, method, path):
        src = _read("core", "api_server.py")
        m = re.search(r'@app\.' + method + r'\("' + re.escape(path) + r'"\)\ndef \w+\([^)]*\):\n(.*?)\n(?=@app\.|\S)',
                      src, re.S)
        self.assertTrue(m, path)
        return m.group(1)

    def test_routes_need_the_admin_session(self):
        for method in ("get", "post"):
            body = self._route(method, "/api/admin/drive-assets/{client_id}/credits")
            first = [ln.strip() for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("#")][0]
            self.assertEqual(first, "_require_admin(request)")
        # _require_admin checks the admin cookie, which a client session never carries
        self.assertIn('verify_admin_session_token(request.cookies.get("admin_session"))', _read("core", "api_server.py"))

    def test_client_dashboard_never_calls_them(self):
        self.assertNotIn("/credits", _read("dashboard", "client", "index.html").replace("credits_", ""))
        self.assertIn("/api/admin/drive-assets/${Number(clientId)}/credits", _read("dashboard", "admin", "index.html"))


HARNESS = r"""
const fs = require('fs'); const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
function check(c, m) { if (!c) { console.error(m); process.exit(1); } }
globalThis.esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
globalThis.localStorage = { getItem: () => '', setItem() {} };
globalThis.crState = { clientId: 27 };
vm.runInThisContext(src);
const el = { innerHTML: '' };
const d = { balance: { free_items: 10, extra_items: 0, allowance: 10, used: 10, remaining: 0 },
  quote: { folders: ['מוצרים (product_catalog)'], remaining: 15, per_item_usd: 0.0001, per_item_ils: 0.00034, quote_usd: 0.0015, quote_ils: 0.01 },
  pending: 15, average: { per_item_ils: 0.00034, items: 10 }, last_run: { at: '2026-11-24T10:00:00Z', items: 10, per_item_usd: 0.0001, per_item_ils: 0.00034 },
  job: { state: 'idle' }, history: [] };
crRenderCredits(el, 27, d);
let h = el.innerHTML;
check(/data-cr-b="remaining">0</.test(h), 'balance 0');
check(h.includes('הצעת מחיר פתוחה') && /data-cr-q="remaining">15</.test(h) && h.includes('מוצרים'), 'open quote');
check(h.includes('id="crCrItems"') && h.includes('value="15"'), 'the amount starts at the quote');
check(h.includes('עוד לא נטענו קרדיטים'), 'empty history');
const after = Object.assign({}, d, { balance: { free_items: 10, extra_items: 15, allowance: 25, used: 10, remaining: 15 }, quote: {},
  job: { state: 'queued', limit: 15 }, history: [{ at: '2026-11-24T11:00:00Z', items: 15, by: 'admin:דנה', note: 'חשבונית 1042', usd: null }] });
crRenderCredits(el, 27, after, 'נטענו 15 פריטים ✓');
h = el.innerHTML;
check(/data-cr-b="remaining">15</.test(h), 'balance after loading');
check(!h.includes('הצעת מחיר פתוחה'), 'quote gone');
check(h.includes('האינדוקס חודש') && h.includes('נשארו 15 פריטים'), 'resumed + remaining');
check(h.includes('דנה') && h.includes('חשבונית 1042') && h.includes('+15 פריטים'), 'history row');
console.log('OK');
"""


@unittest.skipUnless(shutil.which("node"), "Node is not installed")
class Screen(unittest.TestCase):
    def test_renders_balance_quote_resume_and_history(self):
        html = _read("dashboard", "admin", "index.html")
        a = html.index("const CR_CR_JOB")
        code = html[a:html.index("function crCreditsAsk(", a)]
        with tempfile.TemporaryDirectory() as tmp:
            js, h = os.path.join(tmp, "code.js"), os.path.join(tmp, "h.js")
            with open(js, "w", encoding="utf-8") as f:
                f.write(code)
            with open(h, "w", encoding="utf-8") as f:
                f.write(HARNESS)
            run = subprocess.run(["node", h, js], capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)


if __name__ == "__main__":
    unittest.main()
