"""The connection checklist is CAPABILITY-based (core/client_journey).

"The character in your videos" is one step that ANY supported video provider
completes (a filmed likeness via HeyGen, or an AI presenter via Higgsfield);
an avatar package never requires two vendors, and never pulls in the media
step as well. The honesty gate: the AI-presenter path is switched off
(HIGGSFIELD_AVATAR_VIDEO_ENABLED), so a client whose only provider is
Higgsfield sees the step satisfied WITH a plain one-line notice that offers
the alternative — never a silent "done" that would fail on the first video.

Covers: only HeyGen, only Higgsfield (flag off and on), both, neither.

Stdlib only. Run from the repo root:  python -I -B tests/test_avatar_capability_checklist.py -v
"""
import os
import re
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

sys.modules.setdefault("httpx", types.ModuleType("httpx"))
_supabase = types.ModuleType("supabase")
_supabase.create_client = lambda *a, **k: None
sys.modules.setdefault("supabase", _supabase)

# The client store: active platforms per client (no secrets anywhere)
ACTIVE = {}
_client_agent = sys.modules.get("agents.client_agent") or types.ModuleType("agents.client_agent")
_client_agent.get_accounts = lambda cid: [{"platform": p, "status": "active", "has_secret": True}
                                          for p in sorted(ACTIVE.get(cid, ()))]
sys.modules["agents.client_agent"] = _client_agent

from core import avatar_video as av      # noqa: E402
from core import client_journey as cj    # noqa: E402
from core import vendor_tools as vt      # noqa: E402

VENDOR = re.compile(r"heygen|eleven\s?labs|higgsfield", re.IGNORECASE)
FLAG = "HIGGSFIELD_AVATAR_VIDEO_ENABLED"


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class _Query:
    """Just enough of the supabase chain for connection_status."""

    def __init__(self, client_id_box):
        self.box = client_id_box

    def table(self, name):
        return self

    def select(self, *a):
        return self

    def eq(self, column, value):
        if column == "client_id":
            self.box["client_id"] = value
        return self

    def execute(self):
        cid = self.box.get("client_id")
        return types.SimpleNamespace(data=[{"platform": p, "status": "active"}
                                           for p in sorted(ACTIVE.get(cid, ()))])


class CapabilityChecklist(unittest.TestCase):
    PACKAGE = {"id": "p1", "recommended_services": ["avatar"]}

    def setUp(self):
        ACTIVE.clear()
        os.environ.pop(FLAG, None)
        self.saved = (cj._db, cj._chosen_package, vt._record)
        box = {}
        cj._db = lambda: _Query(box)
        cj._chosen_package = lambda cid: dict(self.PACKAGE)
        vt._record = lambda cid: {}

    def tearDown(self):
        cj._db, cj._chosen_package, vt._record = self.saved
        os.environ.pop(FLAG, None)

    def status(self, *platforms):
        ACTIVE[27] = set(platforms)
        return cj.connection_status(27)

    # ── the four cases ──

    def test_neither_connected_asks_for_the_capability(self):
        s = self.status()
        self.assertEqual(s["expected"], ["avatar"])
        self.assertEqual(s["missing"], ["avatar"])
        self.assertEqual(s["next_step"], "avatar")
        self.assertFalse(s["complete"])
        self.assertEqual(s["notices"], [])
        self.assertEqual(s["not_ready"], [])

    def test_only_heygen_completes_it(self):
        s = self.status("heygen")
        self.assertTrue(s["complete"])
        self.assertIsNone(s["next_step"])
        self.assertEqual(s["steps"][0]["ready"], True)
        self.assertEqual(s["notices"], [])
        self.assertEqual(vt.default_provider(27), "heygen")

    def test_only_higgsfield_satisfies_it_but_says_it_cannot_run_yet(self):
        s = self.status("higgsfield")
        # Satisfied: the client is never asked for a second tool
        self.assertTrue(s["complete"])
        self.assertEqual(s["missing"], [])
        self.assertIsNone(s["next_step"])
        # ...but never a silent "done"
        self.assertEqual(s["steps"][0], {"key": "avatar", "platforms": ["heygen", "higgsfield"],
                                         "done": True, "ready": False})
        self.assertEqual(s["not_ready"], ["avatar"])
        (notice,) = s["notices"]
        self.assertEqual(notice["step"], "avatar")
        self.assertEqual(notice["code"], "avatar_video_not_available_yet")
        line = notice["message_he"]
        self.assertNotIn("\n", line)
        self.assertIn("עוד לא זמינים", line)
        self.assertIn("דמות AI", line)
        self.assertIn("דמות מצולמת שלך", line)        # the alternative is offered
        self.assertIsNone(VENDOR.search(line))

    def test_only_higgsfield_video_click_gives_the_same_line_not_a_second_tool(self):
        ACTIVE[27] = {"higgsfield"}
        # No explicit choice: the provider they connected, not HeyGen by default
        self.assertEqual(vt.default_provider(27), "higgsfield")
        result = av.make_video(27, "שלום", {"kind": "recording", "file_id": "x"})
        self.assertEqual(result["code"], "provider_not_ready")
        self.assertNotEqual(result.get("code"), "missing_tool")
        self.assertEqual(result["message_he"], av.not_ready_line("higgsfield"))

    def test_only_higgsfield_with_the_path_enabled_is_simply_ready(self):
        os.environ[FLAG] = "true"
        s = self.status("higgsfield")
        self.assertTrue(s["complete"])
        self.assertTrue(s["steps"][0]["ready"])
        self.assertEqual(s["notices"], [])

    def test_both_connected_is_ready_with_no_notice(self):
        s = self.status("heygen", "higgsfield")
        self.assertTrue(s["complete"])
        self.assertTrue(s["steps"][0]["ready"])
        self.assertEqual(s["notices"], [])
        self.assertEqual(s["not_ready"], [])
        self.assertEqual(vt.default_provider(27), "heygen")

    def test_an_explicit_choice_still_wins(self):
        ACTIVE[27] = {"heygen"}
        vt._record = lambda cid: {"avatar_provider": "higgsfield"}
        self.assertEqual(vt.default_provider(27), "higgsfield")

    def test_unknown_readiness_is_not_readiness(self):
        saved = cj._STEP_NOTICES["avatar"]
        cj._STEP_NOTICES["avatar"] = lambda connected: 1 / 0
        try:
            s = self.status("heygen")
        finally:
            cj._STEP_NOTICES["avatar"] = saved
        self.assertFalse(s["steps"][0]["ready"])
        self.assertEqual(s["not_ready"], ["avatar"])

    # ── one capability, one step, any vendor ──

    def test_avatar_package_does_not_also_require_the_media_tool(self):
        s = self.status()
        self.assertNotIn("media", s["expected"])

    def test_no_package_service_pulls_in_two_steps(self):
        tokens = [t for step in cj.CONNECTION_STEPS for t in step["services"]]
        self.assertEqual(len(tokens), len(set(tokens)), "a service token appears in two steps")
        for step in cj.CONNECTION_STEPS:
            others = [t for other in cj.CONNECTION_STEPS if other is not step for t in other["services"]]
            for token in step["services"]:
                self.assertFalse([o for o in others if token in o or o in token], (step["key"], token))

    def test_every_video_provider_completes_the_avatar_step(self):
        step = next(s for s in cj.CONNECTION_STEPS if s["key"] == "avatar")
        self.assertEqual(set(step["platforms"]), {p.tool for p in av.PROVIDERS.values()})

    def test_one_meta_consent_row_of_any_kind_completes_meta(self):
        self.PACKAGE = {"id": "p1", "recommended_services": ["instagram"]}
        for platform in ("meta_ads", "meta_page", "meta_instagram"):
            self.assertTrue(self.status(platform)["complete"], platform)


class ClientFacingWording(unittest.TestCase):
    def setUp(self):
        self.html = _src("dashboard", "client", "index.html")

    def _i18n(self, key):
        m = re.search(r"^  %s: \{ he:'([^']*)', en:'([^']*)', fr:'([^']*)', ar:'([^']*)', ru:'([^']*)' \},$" % key,
                      self.html, re.M)
        self.assertIsNotNone(m, key)
        return list(m.groups())

    def test_step_is_the_character_in_your_videos(self):
        values = self._i18n("conn_step_avatar")
        self.assertEqual(values[0], "הדמות שבסרטונים שלך")
        self.assertTrue(all(values))
        self.assertEqual([v for v in values if VENDOR.search(v)], [])

    def test_not_ready_notice_in_every_language_without_a_vendor(self):
        values = self._i18n("conn_notice_avatar")
        self.assertTrue(all(values))
        self.assertEqual([v for v in values if VENDOR.search(v)], [])
        self.assertIn("connGuideNotice", self.html)
        self.assertIn("'conn_notice_' + key", self.html)

    def test_both_cards_are_spotlit_for_the_avatar_step(self):
        self.assertIn("avatar: ['toolStatus-heygen', 'toolStatus-higgsfield']", self.html)

    def test_agents_are_told_to_say_it(self):
        self.assertIn('"connected_not_ready"', _src("core", "client_state.py"))
        self.assertIn("connections.connected_not_ready", _src("agents", "support_agent.py"))


if __name__ == "__main__":
    unittest.main()
