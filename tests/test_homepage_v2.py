"""Homepage v2 behind a switch (core/homepage_v2, docs/HOMEPAGE-V2.md,
docs/SYSTEM-REVIEW.md Part 6).

Google's OAuth verification reviews `/`. So:

A. OFF by default, and off = today's page: the page file carries no trace of
   v2, the route hands the request to the same static handler, and a deploy
   alone changes nothing a reviewer sees.
B. ON adds exactly two tags. Everything a reviewer reads — the title, the app
   name, the purpose statement, the services, the privacy and terms links,
   the canonical URL — is the same text in the same place.
C. The switch never makes the public page wait on the database, and a failure
   keeps the current look.
D. The layer itself: motion only without reduced-motion, transforms/opacity,
   no text, no network, no library, passive listeners.

Stdlib only. Run from tests/:  python -I -B test_homepage_v2.py -v
"""
import os
import re
import sys
import threading
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import homepage_v2 as hv   # noqa: E402  (stdlib-only at import)


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _text(html: str) -> str:
    """What a reader reads: tags, scripts and styles removed."""
    html = re.sub(r"<(script|style)\b.*?</\1>", "", html, flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


class OffIsTodaysPage(unittest.TestCase):
    def test_default_is_off(self):
        settings = _src("core", "admin_service.py")
        self.assertIn('"homepage_v2_enabled": False,', settings)
        self.assertFalse(hv._state["on"])                              # a fresh instance starts on today's page

    def test_the_page_file_has_no_trace_of_v2(self):
        page = _src("dashboard", "landing", "index.html")
        for needle in ("home-v2", "homepage_v2", "v2-progress", "?home="):
            self.assertNotIn(needle, page, needle)

    def test_off_goes_through_the_same_static_handler(self):
        api = _src("core", "api_server.py")
        route = api[api.index('@app.api_route("/", methods=["GET", "HEAD"]'):api.index("# Must be last")]
        self.assertIn('return await _landing_static.get_response("index.html", request.scope)', route)
        self.assertIn('os.path.join(BASE_DIR, "dashboard", "landing"), html=True)', api.split("_landing_static = ")[1][:200])
        self.assertIn("except Exception", route)                       # any doubt = today's page
        # the root mount is still the last registration in the file
        self.assertTrue(api.rstrip().endswith('name="landing")'))
        self.assertLess(api.index('@app.api_route("/", methods=["GET", "HEAD"]'), api.rindex('app.mount("/", StaticFiles'))

    def test_no_new_env_var_or_cloud_setting(self):
        code = _src("core", "homepage_v2.py").split('"""', 2)[2]
        for banned in ("os.environ", "getenv", "min-instances", "gcloud"):
            self.assertNotIn(banned, code, banned)
        self.assertNotIn("HOMEPAGE", _src("agents", "keys_agent.py"))

    def test_only_the_homepage_can_load_it(self):
        for page in ("client", "login", "onboarding", "profile", "terms", "privacy", os.path.join("privacy", "he")):
            self.assertNotIn("home-v2", _src("dashboard", page, "index.html"), page)


class OnChangesOnlyTheLook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v1 = _src("dashboard", "landing", "index.html")
        cls.v2 = hv.inject(cls.v1)

    def test_exactly_two_tags_are_added(self):
        self.assertNotEqual(self.v1, self.v2)
        restored = self.v2.replace(hv.HEAD_TAG + "\n", "").replace(hv.BODY_TAG + "\n", "")
        self.assertEqual(restored, self.v1)
        self.assertEqual(self.v2.count("home-v2"), 2)
        self.assertLess(self.v2.index(hv.HEAD_TAG), self.v2.index("</head>"))
        self.assertIn("defer", hv.BODY_TAG)

    def test_everything_a_reviewer_reads_is_identical(self):
        self.assertEqual(_text(self.v1), _text(self.v2))
        for pattern in (r"<title>.*?</title>", r'<meta name="description"[^>]*>', r'<link rel="canonical"[^>]*>',
                        r'<p class="hero-purpose".*?</p>', r"<footer.*?</footer>"):
            a, b = re.search(pattern, self.v1, re.S), re.search(pattern, self.v2, re.S)
            self.assertTrue(a and b, pattern)
            self.assertEqual(a.group(0), b.group(0), pattern)
        links = lambda html: re.findall(r'href="([^"]+)"', html)       # noqa: E731
        self.assertEqual([h for h in links(self.v1)], [h for h in links(self.v2) if "home-v2" not in h])
        for must in ("/privacy/", "/terms/", "uallak", "johnny_support@uallak.com"):
            self.assertIn(must, self.v2, must)

    def test_inject_is_idempotent_and_safe_on_odd_input(self):
        self.assertEqual(hv.inject(self.v2), self.v2)
        self.assertEqual(hv.inject("<p>no head</p>"), "<p>no head</p>")

    def test_preview_parameter_is_exact(self):
        self.assertTrue(hv.wants_preview("v2"))
        for other in (None, "", "V2", "1", "v22", "v2 "):
            self.assertFalse(hv.wants_preview(other), other)
        api = _src("core", "api_server.py")
        route = api[api.index('@app.api_route("/", methods=["GET", "HEAD"]'):api.index("# Must be last")]
        self.assertIn('"Cache-Control": "no-store"', route)            # switching off shows the old page at once

    def test_admin_switch_exists_and_is_saved(self):
        admin = _src("dashboard", "admin", "index.html")
        self.assertIn('id="set-home-v2" type="checkbox"', admin)
        self.assertIn("homepage_v2_enabled: document.getElementById('set-home-v2').checked", admin)
        self.assertIn("/?home=v2", admin)


class SwitchNeverBlocksThePage(unittest.TestCase):
    def setUp(self):
        self.saved = (hv._read_setting, dict(hv._state))
        hv._state.update({"on": False, "at": 0.0, "refreshing": False})

    def tearDown(self):
        hv._read_setting = self.saved[0]
        hv._state.update(self.saved[1])

    def _wait(self):
        deadline = time.time() + 3
        while hv._state["refreshing"] and time.time() < deadline:
            time.sleep(0.01)

    def test_a_slow_database_costs_the_page_nothing(self):
        gate = threading.Event()

        def slow():
            gate.wait(2)
            return True
        hv._read_setting = slow
        started = time.perf_counter()
        self.assertFalse(hv.enabled())                                  # answered at once: today's page
        self.assertLess(time.perf_counter() - started, 0.2)
        self.assertFalse(hv.enabled())                                  # still refreshing: no second thread
        self.assertEqual(sum(t.name == "homepage-v2-flag" for t in threading.enumerate()), 1)
        gate.set()
        self._wait()
        self.assertTrue(hv.enabled())                                   # the answer arrived for the next request

    def test_a_failed_read_keeps_the_current_look(self):
        def boom():
            raise RuntimeError("db down")
        hv._read_setting = boom
        hv.enabled(); self._wait()
        self.assertFalse(hv.enabled())
        hv._state.update({"on": True, "at": 0.0})
        hv.enabled(); self._wait()
        self.assertTrue(hv._state["on"])                                # was on, stays on

    def test_fresh_answer_is_not_refetched(self):
        calls = []
        hv._read_setting = lambda: calls.append(1) or False
        hv.enabled(); self._wait()
        hv.enabled(); hv.enabled(); self._wait()
        self.assertEqual(len(calls), 1)

    def test_values_that_mean_on(self):
        for value in (True, "true", "on", "1", "yes"):
            self.assertTrue(hv._truthy(value), value)
        for value in (False, None, "", "false", "off", 0, "0"):
            self.assertFalse(hv._truthy(value), value)


class TheLayer(unittest.TestCase):
    def setUp(self):
        self.css = _src("dashboard", "assets", "home-v2.css")
        self.js = _src("dashboard", "assets", "home-v2.js").split("*/", 1)[1]

    def test_css_adds_no_text_and_no_outside_resource(self):
        rules = self.css.split("*/", 1)[1]
        self.assertEqual(set(re.findall(r"content:\s*([^;]+);", rules)), {"''"})
        for banned in ("url(", "@import", "@font-face", "display: none !important", "visibility: hidden"):
            self.assertNotIn(banned, rules, banned)

    def test_every_animation_waits_for_no_preference(self):
        rules = self.css.split("*/", 1)[1]
        motion = rules[rules.index("@media (prefers-reduced-motion: no-preference)"):
                       rules.index("@media (prefers-reduced-motion: reduce)")]
        outside = rules.replace(motion, "")
        self.assertNotIn("animation:", outside)
        self.assertNotIn("@keyframes", outside)
        for block in re.findall(r"@keyframes \w+ \{(.*?\})\s*\}", motion, re.S):
            props = set(re.findall(r"([a-z-]+)\s*:", re.sub(r"\([^)]*\)", "", block)))
            self.assertTrue(props <= {"transform", "opacity", "background-position"}, props)

    def test_script_is_light_and_never_rewrites_content(self):
        for banned in ("fetch(", "XMLHttpRequest", "localStorage", "sessionStorage", "import ", "require(",
                       "innerHTML", "textContent", "innerText", "setInterval(", "http", "cookie"):
            self.assertNotIn(banned, self.js, banned)
        self.assertIn("if (reduce) return;", self.js)
        self.assertEqual(self.js.count("appendChild("), 1)              # the progress line, aria-hidden
        self.assertIn("bar.setAttribute('aria-hidden', 'true')", self.js)
        self.assertGreaterEqual(self.js.count("{ passive: true }"), 5)
        self.assertIn("requestAnimationFrame(onScrollFrame)", self.js)
        self.assertIn("document.hidden", self.js)

    def test_small_files(self):
        for name in ("home-v2.css", "home-v2.js"):
            self.assertLess(os.path.getsize(os.path.join(ROOT, "dashboard", "assets", name)), 12000, name)


if __name__ == "__main__":
    unittest.main()
