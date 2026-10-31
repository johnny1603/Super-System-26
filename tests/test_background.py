"""The animated triangle background (dashboard/assets/uallak-bg.js + .css).

One shared asset, loaded by the four client-facing pages (dashboard, landing,
login, sales chat) and never by admin / Control Room; a layer that can never
sit above content or take a click; the tuned values from the design file;
the performance and reduced-motion rules. Browser behaviour (stacking, no
horizontal scroll, hidden-tab stop) was checked by hand — see the commit.

Run from the tests folder:  python -I -B test_background.py -v
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS_TAG = '<link rel="stylesheet" href="/assets/uallak-bg.css">'
JS_TAG = '<script src="/assets/uallak-bg.js" defer></script>'
WITH_BG = ("client", "landing", "login", "onboarding")
WITHOUT_BG = ("admin", "profile", "terms", "privacy")


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Pages(unittest.TestCase):
    def test_client_facing_pages_load_it_once(self):
        for page in WITH_BG:
            html = _src("dashboard", page, "index.html")
            self.assertEqual(html.count(CSS_TAG), 1, page)
            self.assertEqual(html.count(JS_TAG), 1, page)
            self.assertLess(html.index(JS_TAG), html.index("</head>"), page)

    def test_admin_and_control_room_stay_plain(self):
        for page in WITHOUT_BG:
            html = _src("dashboard", page, "index.html")
            self.assertNotIn("uallak-bg", html, page)

    def test_no_html_background_so_the_layer_shows(self):
        # z-index:-1 sits above the canvas only while <html> has no background
        # of its own (body's background then paints the canvas)
        for page in WITH_BG:
            html = _src("dashboard", page, "index.html")
            for rule in re.findall(r"(?:^|[\s}])html\s*(?:,[^{]*)?\{([^}]*)\}", html):
                self.assertNotRegex(rule, r"background", page)

    def test_design_file_is_gone_and_unreferenced(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "uallak-bg.html")))
        for folder in ("core", "agents", "dashboard"):
            for dirpath, _, files in os.walk(os.path.join(ROOT, folder)):
                for name in files:
                    if name.endswith((".py", ".html", ".js", ".css")):
                        with open(os.path.join(dirpath, name), encoding="utf-8", errors="ignore") as f:
                            self.assertNotIn("uallak-bg.html", f.read(), name)


class Layer(unittest.TestCase):
    def setUp(self):
        self.css = _src("dashboard", "assets", "uallak-bg.css")
        self.js = _src("dashboard", "assets", "uallak-bg.js")

    def test_behind_everything_and_never_clickable(self):
        rule = self.css.split(".uk-bg {", 1)[1].split("}", 1)[0]
        for decl in ("position: fixed", "inset: 0", "z-index: -1", "pointer-events: none", "overflow: hidden"):
            self.assertIn(decl, rule)
        self.assertIn("aria-hidden", self.js)

    def test_only_the_triangles(self):
        for gone in ("aurora", "uk-grid", "picker", "@keyframes"):
            self.assertNotIn(gone, self.css + self.js)

    def test_tuned_values_unchanged(self):
        for value in ("var SHARDS = 22", "9 + Math.random() * 26", "0.72 + Math.random() * 0.45",
                      "(Math.random() - 0.5) * 0.0045", "-(0.09 + Math.random() * 0.17)",
                      "-(0.13 + Math.random() * 0.24)", "'193,39,45'", "'255,76,31'", "< 0.25",
                      "(s.y - h * 0.42) / (h * 0.55)", "* 0.5;", "s.y < h * 0.3", "ctx.lineWidth = 1.1",
                      "a * 0.13"):
            self.assertIn(value, self.js, value)

    def test_performance_and_reduced_motion(self):
        self.assertIn("Math.min(window.devicePixelRatio || 1, 2)", self.js)
        self.assertIn("SHARDS_SMALL = 12", self.js)
        self.assertIn("w < SMALL_WIDTH ? SHARDS_SMALL : SHARDS", self.js)
        self.assertIn("'visibilitychange'", self.js)
        self.assertIn("if (document.hidden) stop(); else run();", self.js)
        self.assertIn("'(prefers-reduced-motion: reduce)'", self.js)
        run = self.js.split("function run()", 1)[1].split("\n    }", 1)[0]
        self.assertIn("if (reduced()) { draw(false); return; }", run)   # a still frame, no loop
        self.assertIn("if (window.__ukBg) return;", self.js)             # one layer even if loaded twice


if __name__ == "__main__":
    unittest.main()
