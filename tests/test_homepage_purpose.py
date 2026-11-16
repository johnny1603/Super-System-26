"""The live homepage explains the app's purpose and its Google data (2026-11-17).

Google's OAuth verification was blocked on "Your homepage does not explain the
purpose of your app". What must stay true on the page served at "/"
(dashboard/landing/index.html, the v1 page Google reviews):
- a section near the top (before the services), in the HTML SOURCE, in English
  AND Hebrew, that says what uallak is, who it is for, each of the six Google
  scopes in plain words, and that the owner approves every action;
- nothing in it is translated, swapped or hidden by script (no data-i18n, no
  .reveal), and it is not an image;
- the privacy policy and terms links stay on the page.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import google_scopes   # noqa: E402


def _page() -> str:
    with open(os.path.join(ROOT, "dashboard", "landing", "index.html"), encoding="utf-8") as f:
        return f.read()


def _section(page: str) -> str:
    start = page.index('<section class="app-purpose" id="about-uallak"')
    return page[start:page.index("</section>", start)]


class Purpose(unittest.TestCase):
    def test_near_the_top_in_the_source(self):
        page = _page()
        at = page.index('id="about-uallak"')
        self.assertLess(page.index('<section class="hero">'), at)
        self.assertLess(at, page.index('<section id="services">'))

    def test_every_scope_in_both_languages(self):
        section = _section(_page())
        english = section[section.index('lang="en"'):section.index('lang="he"')]
        hebrew = section[section.index('lang="he"'):]
        for scope in google_scopes.GOOGLE_SCOPES:
            short = scope.rsplit("/", 1)[1]
            self.assertIn(f"<code>{short}</code>", english, short)
            self.assertIn(f"<code>{short}</code>", hebrew, short)
        for needle in ("AI marketing management platform for small and medium businesses", "business owners",
                       "own", "approves every action", "Google Ads", "YouTube", "Tag Manager"):
            self.assertIn(needle, english, needle)
        for needle in ("פלטפורמה לניהול שיווק", "לעסקים קטנים ובינוניים", "בעל העסק מאשר כל פעולה"):
            self.assertIn(needle, hebrew, needle)

    def test_never_translated_hidden_or_an_image(self):
        section = _section(_page())
        # data-i18n-skip only marks the Hebrew block as Hebrew on purpose (i18n.js never acts on it)
        self.assertEqual(re.findall(r"data-i18n[\w-]*", section), ["data-i18n-skip"])
        self.assertNotIn("reveal", section)
        self.assertNotIn("<img", section)
        self.assertNotIn("hidden", section)
        self.assertNotIn("display:none", section.replace(" ", ""))

    def test_policy_links_and_name_stay(self):
        page = _page()
        section = _section(page)
        for link in ('href="/privacy/"', 'href="/privacy/he/"', 'href="/terms/"'):
            self.assertIn(link, section)
        footer = page[page.index("<footer"):page.index("</footer>")]
        self.assertIn('href="/terms/"', footer)
        self.assertIn('href="/privacy/he/"', footer)
        self.assertRegex(page, r"<title>uallak")
        self.assertIn('class="nav-logo">u<span>allak</span>', page)

    def test_no_vendor_or_promise_words(self):
        section = _section(_page()).lower()
        for word in ("guarantee", "higgsfield", "semrush", "ahrefs", "מבטיח"):
            self.assertNotIn(word, section)
        self.assertIsNone(re.search(r"publish(es|ed)? (on|for) you", section))


if __name__ == "__main__":
    unittest.main()
