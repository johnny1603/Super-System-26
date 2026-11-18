"""Full language separation (2026-11-19, docs/LANGUAGES.md).

- one source for the client's language (core/client_language), passed on every model call;
- server-built lines go through 5-language tables in the client's language;
- no hard-coded Hebrew in a client-facing module (small allow-list), and the
  not-yet-swept modules may only get better (KNOWN_DEBT ratchet);
- numbers / money / dates per language, RTL only for he + ar;
- the owner's gaps report.
"""
import os
import sys
import types
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
os.environ.setdefault("SESSION_SECRET_KEY", "test-only-not-a-secret")
for _name, _attrs in (("anthropic", {"Anthropic": lambda *a, **k: None}),
                      ("supabase", {"create_client": lambda *a, **k: None})):
    if _name not in sys.modules:
        _stub = types.ModuleType(_name)
        _stub.__dict__.update(_attrs)
        sys.modules[_name] = _stub
try:
    import httpx  # noqa: F401
except ImportError:                    # stdlib-only run: nothing here touches the network
    _httpx = types.ModuleType("httpx")
    _httpx.__getattr__ = lambda name: type(name, (Exception,), {})
    sys.modules["httpx"] = _httpx
from core import client_language as cl  # noqa: E402
from core import i18n_gaps  # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class TheClientsLanguage(unittest.TestCase):
    def setUp(self):
        cl._cache.clear()
        self._saved = cl._stored

    def tearDown(self):
        cl._stored = self._saved
        cl._cache.clear()

    def test_setting_wins_and_unreadable_is_hebrew(self):
        cl._stored = lambda cid: "fr"
        self.assertEqual(cl.of(27), "fr")
        self.assertEqual(cl.resolve(27, None), "fr")
        self.assertEqual(cl.resolve(27, "ru"), "ru")          # an explicit language wins
        self.assertEqual(cl.resolve(27, "xx"), "fr")
        cl._cache.clear()

        def boom(cid):
            raise RuntimeError("db down")
        cl._stored = boom
        self.assertEqual(cl.of(28), "he")
        self.assertNotIn(28, cl._cache)                       # a failure is never cached

    def test_the_reply_follows_the_language_the_client_writes_in(self):
        cl._stored = lambda cid: "he"
        self.assertEqual(cl.for_message(27, "Can you check my store?"), "en")
        self.assertEqual(cl.for_message(27, "Привет, как дела?"), "ru")
        self.assertEqual(cl.for_message(27, "مرحبا"), "ar")
        self.assertEqual(cl.for_message(27, "123 👍"), "he")
        cl._cache.clear()
        cl._stored = lambda cid: "fr"
        self.assertEqual(cl.for_message(27, "Bonjour, une question"), "fr")   # Latin: the setting breaks the tie

    def test_every_chat_call_carries_reply_language(self):
        src = _read("agents", "support_agent.py")
        self.assertGreaterEqual(src.count('"reply_language": client_language.payload('), 2)
        self.assertIn("REPLY_LANGUAGE_RULE", src)
        self.assertIn("never switch back to hebrew", cl.REPLY_LANGUAGE_RULE.lower())
        self.assertIn("reply_language", _read("agents", "morning_briefing.py"))

    def test_no_default_hebrew_in_server_built_lines(self):
        # the swept functions take lang=None and resolve it from the client
        for path in ("core/quarter_goals.py", "core/organic_plan.py", "core/home_focus.py",
                     "core/content_team.py", "core/manager_identity.py"):
            src = _read(*path.split("/"))
            self.assertIn("_cl.resolve(client_id, lang)", src, path)


class Formats(unittest.TestCase):
    def test_direction(self):
        self.assertEqual([cl.direction(x) for x in LANGS], ["rtl", "ltr", "ltr", "rtl", "ltr"])
        self.assertEqual(cl.payload("ar")["direction"], "rtl")

    def test_numbers_money_dates(self):
        self.assertEqual(cl.fmt_number(1234567, "en"), "1,234,567")
        self.assertEqual(cl.fmt_number(1234.5, "fr").replace("\u202f", " ").replace("\u00a0", " "), "1 234,5")
        self.assertEqual(cl.fmt_money(1200, "he"), "₪1,200")
        self.assertEqual(cl.fmt_money(1200, "ru").replace("\u202f", " ").replace("\u00a0", " "), "1 200 ₪")
        self.assertEqual(cl.fmt_date("2026-08-01", "en"), "01/08/2026")
        self.assertEqual(cl.fmt_date("2026-08-01T00:00:00Z", "ru"), "01.08.2026")

    def test_hebrew_only_items_get_one_line(self):
        self.assertEqual(cl.hebrew_only_note("he"), "")
        for lang in LANGS[1:]:
            self.assertTrue(cl.hebrew_only_note(lang))
            self.assertNotRegex(cl.hebrew_only_note(lang), r"[֐-׿]")


class ServerLinesAreTranslated(unittest.TestCase):
    def test_upload_refusals(self):
        from core import chat_uploads as cu
        for code in cu.PLAIN:
            for lang in LANGS:
                self.assertTrue(cu.plain(code, lang), (code, lang))
        self.assertEqual(cu.PLAIN_HE["empty"], cu.plain("empty", "he"))
        self.assertNotRegex(cu.plain("storage", "en"), r"[֐-׿]")
        self.assertIn('"message": cu.plain(e.code', _read("core", "api_server.py"))
        self.assertIn("detail.message ||", _read("dashboard", "client", "index.html"))

    def test_store_lines(self):
        from agents import store_agent as sa
        for code in sa.ERRORS:
            for lang in LANGS:
                self.assertTrue(sa.error_text(code, lang))
        self.assertEqual(set(sa.STATUS_TEXT), set(sa.STATUS_HE))

    def test_proposal_reply(self):
        from agents import support_agent as sup
        proposal = {"packages": [{"name": "Pro", "monthly_management_total": 1200, "setup_fee_total": 500}]}
        for lang in LANGS[1:]:
            text = sup._format_proposal_reply(proposal, lang)
            self.assertNotRegex(text, r"[֐-׿]", lang)
        self.assertIn(cl.fmt_money(1200, "fr"), sup._format_proposal_reply(proposal, "fr"))

    def test_tables_have_every_language(self):
        gaps = {}
        for path in i18n_gaps.CLIENT_FACING:
            for lang, rows in i18n_gaps.table_gaps(path).items():
                gaps.setdefault(path, []).extend((lang, line) for line, _ in rows)
        self.assertEqual(gaps, {})


class NoHardCodedHebrew(unittest.TestCase):
    def test_client_facing_modules_are_clean(self):
        found = i18n_gaps.violations()
        self.assertEqual(found, [], "\n".join(f"{f}:{line} {text[:60]}" for f, line, text, _ in found))

    def test_the_allow_list_stays_small(self):
        self.assertLessEqual(len(i18n_gaps.ALLOW), 60)

    def test_the_scanner_still_catches_one(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write('def reply(cid):\n    return "שלום, ההזמנה התקבלה"\n'
                    'TABLE = {"he": "שלום", "en": "Hi", "fr": "Salut", "ar": "مرحبا", "ru": "Привет"}\n')
        try:
            rows = i18n_gaps.hardcoded(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 2)

    def test_known_debt_only_goes_down(self):
        worse = []
        for path, baseline in i18n_gaps.KNOWN_DEBT.items():
            now = len(i18n_gaps.hardcoded(path))
            if now > baseline:
                worse.append(f"{path}: {now} > {baseline}")
        self.assertEqual(worse, [], "new hard-coded Hebrew: move it into a 5-language table")
        self.assertFalse(set(i18n_gaps.KNOWN_DEBT) & set(i18n_gaps.CLIENT_FACING))


class GapsReport(unittest.TestCase):
    def test_report_shape_and_admin_page(self):
        r = i18n_gaps.report()
        self.assertEqual(r["languages"], list(LANGS))
        for lang in LANGS[1:]:
            self.assertIn(lang, r["per_language"])
        self.assertIn("not_yet_swept", r)
        api = _read("core", "api_server.py")
        self.assertIn('"/api/admin/i18n/gaps"', api)
        page = _read("dashboard", "admin", "i18n", "index.html")
        self.assertIn("/api/admin/i18n/gaps", page)


if __name__ == "__main__":
    unittest.main()
