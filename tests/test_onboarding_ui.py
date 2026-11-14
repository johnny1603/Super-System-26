"""The compact onboarding hub on the client dashboard (docs/ONBOARDING-UI.md,
docs/SYSTEM-REVIEW.md Part 5).

A. The logic of what is required did not move: the hub renders the server's
   checklist; core/client_journey is untouched by this part; with an
   unreadable package there is no hub and no grouping.
B. Markup + style: ring, chips, one next action, group headers inside the
   grid, folded "connected" and "more" groups, texts in five languages.
C. One decision per card: every connection card kept its single connect
   button; cards are grouped by attribute + CSS order, never moved.
D. Behaviour (the real grouping function in Node against a fake grid).

Run from the tests folder:  python -I -B test_onboarding_ui.py -v
"""
import json
import os
import re
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NODE = shutil.which("node")
PAGE_PATH = os.path.join(ROOT, "dashboard", "client", "index.html")
KEYS = ("ob_next_label", "ob_group_todo", "ob_group_done", "ob_group_more", "ob_show", "ob_hide",
        "ob_step_completed", "journey_last", "journey_next", "journey_all_done", "journey_show_all",
        "journey_hide_all")


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Page(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = _src("dashboard", "client", "index.html")
        cls.guide = cls.page.split("function renderConnectionGuide(status) {", 1)[1].split("\n}\n", 1)[0]
        cls.layout = cls.page.split("function layoutConnCards() {", 1)[1].split("\n}\n", 1)[0]

    # ── A. the required logic stays on the server ──
    def test_the_hub_only_draws_the_servers_checklist(self):
        for field in ("status.done_count", "status.total_count", "status.complete", "status.next_step"):
            self.assertIn(field, self.guide + self.page.split("function renderObSteps(status) {", 1)[1][:900], field)
        self.assertIn("fetch('/api/client/connection-checklist'", self.page)
        # nothing in the page decides what a package requires
        for banned in ("recommended_services", "required_connections", "CONNECTION_STEPS"):
            self.assertNotIn(banned, self.page, banned)

    def test_unreadable_package_means_no_hub_and_no_grouping(self):
        early = self.guide[:self.guide.index("guide.style.display = 'grid'")]
        self.assertIn("guide.style.display = 'none'", early)
        self.assertIn("OB_STATUS = null;", early)
        self.assertIn("layoutConnCards();", early)
        plain = self.layout[:self.layout.index("const rank")]
        self.assertIn("removeAttribute('data-ob')", plain)
        self.assertIn("h.hidden = true", plain)

    def test_interview_and_nudge_flow_untouched(self):
        flow = self.page.split("async function runConnectionFlow(isFirstLogin) {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("if (status.complete) {", flow)
        self.assertIn("startInterview();", flow)
        self.assertIn("if (isFirstLogin) runConnectionNudge();", flow)

    # ── B. markup, style, languages ──
    def test_hub_markup(self):
        hub = self.page[self.page.index('<div class="conn-guide ob-hub" id="connGuide"'):
                        self.page.index('<div class="conn-grid" id="connGrid">')]
        for needle in ('id="obRing"', 'id="obRingFill"', 'id="connGuideCount"', 'id="connGuideTitle"',
                       'id="connGuideText"', 'id="connGuideNotice"', 'id="obSteps"',
                       'onclick="scrollToNextConnCard()"'):
            self.assertIn(needle, hub, needle)
        self.assertEqual(hub.count("<button"), 1)                        # ONE next action in the hub

    def test_group_headers_live_in_the_grid_and_two_groups_fold(self):
        grid = self.page[self.page.index('<div class="conn-grid" id="connGrid">'):][:1600]
        for group in ("next", "todo", "done", "more"):
            self.assertIn(f'data-ob-head="{group}" hidden', grid)
        self.assertIn("toggleConnGroup('done')", grid)
        self.assertIn("toggleConnGroup('more')", grid)
        self.assertIn('#connGrid:not(.show-done) > .conn-card[data-ob="done"]', self.page)
        self.assertIn('#connGrid:not(.show-more) > .conn-card[data-ob="more"]', self.page)
        orders = {g: int(re.search(r'\.conn-card\[data-ob="%s"\] \{ order: (\d+); \}' % g, self.page).group(1))
                  for g in ("next", "todo", "done", "more")}
        self.assertTrue(orders["next"] < orders["todo"] < orders["done"] < orders["more"])

    def test_texts_in_five_languages(self):
        for key in KEYS:
            line = next((ln for ln in self.page.splitlines() if ln.strip().startswith(key + ":")), "")
            self.assertTrue(line, key)
            for lang in ("he:", "en:", "fr:", "ar:", "ru:"):
                self.assertIn(lang, line, key)

    def test_motion_respects_the_client(self):
        css = self.page[self.page.index("/* ---------- ONBOARDING HUB"):self.page.index("/* ---------- PROGRESS JOURNEY")]
        self.assertLess(css.index("@media (prefers-reduced-motion: no-preference)"), css.index("@keyframes obPulse"))
        celebrate = self.page.split("function obCelebrateProgress(status) {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("ADMIN_VIEW", celebrate)                          # never for staff viewing as the client
        self.assertIn("before === null", celebrate)                     # the first visit only records
        self.assertIn("window.uallakLife.celebrate(ring,", celebrate)   # one-per-key, reduced-motion aware

    def test_language_switch_repaints_the_hub(self):
        line = "if (OB_STATUS) renderConnectionGuide(OB_STATUS); else layoutConnCards();"
        self.assertIn(line, self.page)
        handler = self.page[:self.page.index(line)].rsplit("uallakI18n.onChange(() => {", 1)[1]
        self.assertLess(len(handler), 400)                  # it sits inside the language-change handler

    # ── C. one decision per card, nothing moved ──
    def test_every_card_kept_one_connect_button(self):
        grid = self.page[self.page.index('<div class="conn-grid" id="connGrid">'):self.page.index('data-i18n="perf_title"')
                         if 'data-i18n="perf_title"' in self.page else len(self.page)]
        cards = grid.split('<div class="conn-card')[1:]
        self.assertGreaterEqual(len(cards), 12)
        for card in cards[:17]:
            self.assertLessEqual(card.count('onclick="connectViaChat('), 1)

    def test_cards_are_grouped_by_attribute_never_moved(self):
        block = self.page[self.page.index("// ─── Onboarding hub: ring, step chips, grouped cards"):
                          self.page.index("function obCelebrateProgress(status) {")]
        for move in ("insertBefore(", "prepend(", ".after(", ".before(", "replaceWith("):
            self.assertNotIn(move, block, move)
        self.assertEqual(block.count("appendChild("), 1)                # the step chips only
        self.assertIn("card.dataset.ob = group", block)

    def test_a_folded_card_is_never_a_dead_end(self):
        self.assertGreaterEqual(self.page.count("connReveal("), 8)
        for fn in ("function focusToolCard(tool) {", "function showStoreCard() {", "function scrollToNextConnCard() {"):
            body = self.page.split(fn, 1)[1].split("\n}\n", 1)[0]
            self.assertLess(body.index("connReveal("), body.index("scrollIntoView("), fn)


HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const page = fs.readFileSync(process.argv[1], 'utf8');
const cut = (from, to) => page.slice(page.indexOf(from), page.indexOf(to));
const code = cut('const CONN_STEP_ANCHOR = {', 'function highlightNextConnCard(stepKey) {')
           + cut('function layoutConnCards() {', '// Anything that sends the client TO a connection card');
function node(cls, extra) {
  const classes = new Set(cls.split(' ')), attrs = {};
  const n = Object.assign({ style: {}, hidden: false, children: [], textContent: '',
    classList: { contains: c => classes.has(c), add: c => classes.add(c), toggle: c => (classes.has(c) ? classes.delete(c) : classes.add(c)) },
    dataset: new Proxy({}, { get: (_t, k) => attrs['data-' + k], set: (_t, k, v) => { attrs['data-' + k] = String(v); return true; } }),
    hasAttribute: a => a in attrs, removeAttribute: a => { delete attrs[a]; }, setAttribute: (a, v) => { attrs[a] = v; },
    attr: a => attrs[a] }, extra || {});
  return n;
}
function world(connected) {
  const ids = {}, cards = {};
  const card = (name, statusId) => {
    const status = node('conn-status' + (connected.includes(name) ? ' connected' : ''));
    const c = node('conn-card', { name, querySelector: sel => (sel === '.conn-status.connected' && status.classList.contains('connected') ? status : null) });
    status.closest = () => c;
    if (statusId) ids[statusId] = status;
    cards[name] = c;
    return c;
  };
  const head = g => { const label = node('x'), toggle = node('ob-toggle');
    const h = node('ob-group', { g, label, toggle, querySelector: sel => (sel === '[data-ob-count]' ? (g === 'done' || g === 'more' ? label : null)
                                                                     : (g === 'done' || g === 'more' ? toggle : null)) });
    h.dataset.obHead = g; return h; };
  const grid = node('conn-grid');
  grid.children = [head('next'), head('todo'), head('done'), head('more'),
    card('google', 'gadsStatus'), card('meta', 'metaStatus'), card('tiktok', 'tiktokStatus'), card('youtube', 'youtubeStatus'),
    card('site', 'siteStatus'), card('media', 'toolStatus-higgsfield'), card('voice', null), card('crm', null), card('hidden', null)];
  cards.hidden.style.display = 'none';
  ids.connGrid = grid;
  const ctx = { document: { getElementById: id => ids[id] || null }, OB_STATUS: null,
                uallakI18n: { t: (k, p) => k + (p && p.n !== undefined ? ':' + p.n : '') } };
  vm.runInNewContext(code + '\nthis.run = s => { OB_STATUS = s; layoutConnCards(); };', ctx);
  const view = () => ({ groups: Object.fromEntries(Object.entries(cards).map(([n, c]) => [n, c.attr('data-ob') || null])),
                        heads: Object.fromEntries(grid.children.filter(x => x.g).map(h => [h.g, { hidden: h.hidden, label: h.label.textContent, toggle: h.toggle.textContent }])) });
  return { ctx, grid, view };
}
const steps = (done, next) => ({ next_step: next, steps: ['google_ads', 'meta', 'website', 'media', 'avatar'].map(k => ({ key: k, done: done.includes(k) })) });
const out = {};
{ const w = world(['google', 'voice']); w.ctx.run(steps(['google_ads'], 'meta')); out.mid = w.view(); }
{ const w = world(['google', 'meta', 'site', 'media']); w.ctx.run(Object.assign(steps(['google_ads', 'meta', 'website', 'media', 'avatar'], null), { complete: true })); out.complete = w.view(); }
{ const w = world(['google']); w.ctx.run(steps(['google_ads'], 'meta')); w.ctx.run(null); out.unreadable = w.view(); }
{ const w = world([]); w.ctx.run({ next_step: null, steps: [] }); out.empty = w.view(); }
{ const w = world(['google']); w.grid.classList.add('show-done'); w.ctx.run(steps(['google_ads'], 'meta')); out.opened = w.view().heads.done; }
// one card serving two steps (the media tool is both "media" and "avatar"): the more urgent state wins
{ const w = world([]); w.ctx.run(steps(['google_ads', 'meta', 'website', 'media'], 'avatar')); out.shared = w.view().groups.media; }
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(NODE, "node is not installed — the page script can't run here")
class Grouping(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = subprocess.run([NODE, "-e", HARNESS, PAGE_PATH], capture_output=True, text=True, encoding="utf-8",
                             timeout=60)
        if run.returncode != 0:
            raise AssertionError(run.stderr[-3000:])
        cls.out = json.loads(run.stdout.strip().splitlines()[-1])

    def test_mid_onboarding(self):
        groups, heads = self.out["mid"]["groups"], self.out["mid"]["heads"]
        self.assertEqual(groups["meta"], "next")                         # the ONE next step
        self.assertEqual(groups["google"], "done")
        self.assertEqual(groups["site"], "todo")
        self.assertEqual(groups["media"], "todo")
        self.assertEqual(groups["voice"], "done")                        # connected, though not a required step
        self.assertEqual((groups["tiktok"], groups["youtube"], groups["crm"]), ("more", "more", "more"))
        self.assertEqual([heads[g]["hidden"] for g in ("next", "todo", "done", "more")], [False] * 4)
        self.assertEqual(heads["done"]["label"], "ob_group_done:2")
        self.assertEqual(heads["more"]["label"], "ob_group_more:3")      # the hidden card is not counted
        self.assertEqual(heads["done"]["toggle"], "ob_show")

    def test_everything_connected(self):
        groups, heads = self.out["complete"]["groups"], self.out["complete"]["heads"]
        self.assertNotIn("next", groups.values())
        self.assertNotIn("todo", groups.values())
        self.assertTrue(heads["next"]["hidden"] and heads["todo"]["hidden"])
        self.assertFalse(heads["done"]["hidden"])

    def test_no_grouping_without_a_readable_package(self):
        for case in ("unreadable", "empty"):
            self.assertEqual(set(self.out[case]["groups"].values()), {None}, case)
            self.assertTrue(all(h["hidden"] for h in self.out[case]["heads"].values()), case)

    def test_open_group_says_hide_and_shared_card_takes_the_urgent_state(self):
        self.assertEqual(self.out["opened"]["toggle"], "ob_hide")
        self.assertEqual(self.out["shared"], "next")


if __name__ == "__main__":
    unittest.main()
