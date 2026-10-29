"""TikTok scopes after the 2026-10-29 app-review rejection: only
user.info.basic + video.publish are requested, and the engagement reads
(video.list) sit behind tiktok_service.ENGAGEMENT_READS_ENABLED. Also the
public landing page's review checklist (links, contact, no invisible
characters, every string has an English version).

Stdlib only. core.tiktok_service and agents.tiktok_content_agent are
imported for real with httpx / supabase / keys / alerts replaced by stubs, so
what runs is the actual flag logic — and any HTTP call would be counted.

Run from the repo root:  python tests/test_tiktok_scopes.py -v
"""
import os
import re
import sys
import types
import unittest
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_STUBBED = ("httpx", "supabase", "agents.keys_agent", "core.agent_base", "core.secrets_vault",
            "agents.client_agent", "core.tiktok_service", "agents.tiktok_content_agent")


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


class _Response:
    status_code = 200

    def json(self):
        return {"data": {"videos": []}, "error": {"code": "ok"}}


class TikTokCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved_modules = {n: sys.modules.get(n) for n in _STUBBED}
        cls.http_calls, cls.alerts, cls.upserts = [], [], []

        def http(method):
            def call(*a, **k):
                cls.http_calls.append((method, a[0] if a else k.get("url")))
                return _Response()
            return call

        sys.modules.update({
            "httpx": _module("httpx", post=http("POST"), get=http("GET"), put=http("PUT"),
                             Response=_Response),
            "supabase": _module("supabase", create_client=lambda *a: None),
            "agents.keys_agent": _module("agents.keys_agent", get_key=lambda k: "key-" + k),
            "core.agent_base": _module("core.agent_base",
                                       agent_alert=lambda src, issues: cls.alerts.append(issues),
                                       log_step=lambda *a, **k: None,
                                       timed_step=lambda agent, step, fn: fn()),
            "core.secrets_vault": _module("core.secrets_vault", hydrate=lambda row: row),
            "agents.client_agent": _module("agents.client_agent",
                                           upsert_account=lambda *a: cls.upserts.append(a)),
        })
        for name in ("core.tiktok_service", "agents.tiktok_content_agent"):
            sys.modules.pop(name, None)
        import core.tiktok_service as service
        import agents.tiktok_content_agent as agent
        cls.service, cls.agent = service, agent

    @classmethod
    def tearDownClass(cls):
        for name, module in cls._saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def setUp(self):
        self.http_calls.clear()
        self.alerts.clear()
        self.upserts.clear()
        self.agent._get_connection = lambda cid: {"client_id": cid, "account_id": "open-id-1",
                                                  "access_token": "acc::ref"}


class Scopes(TikTokCase):
    def test_flag_is_off(self):
        self.assertFalse(self.service.ENGAGEMENT_READS_ENABLED)

    def test_only_login_and_publish_scopes(self):
        self.assertEqual(self.service.OAUTH_SCOPES, ["user.info.basic", "video.publish"])

    def test_consent_url_does_not_ask_for_video_list(self):
        query = parse_qs(urlparse(self.service.build_consent_url("st")).query)
        self.assertEqual(query["scope"], ["user.info.basic,video.publish"])
        self.assertNotIn("video.list", query["scope"][0])


class EngagementOff(TikTokCase):
    def test_list_and_query_refuse_before_any_http(self):
        with self.assertRaises(self.service.EngagementReadsDisabled):
            self.service.list_videos("acc")
        with self.assertRaises(self.service.EngagementReadsDisabled):
            self.service.query_videos("acc", ["1"])
        self.assertEqual(self.http_calls, [])

    def test_disabled_is_still_a_tiktok_api_error(self):
        self.assertTrue(issubclass(self.service.EngagementReadsDisabled, self.service.TikTokAPIError))

    def test_summary_is_a_fact_with_no_call_and_no_alert(self):
        result = self.agent.get_engagement_summary(27)
        self.assertEqual(result, {"connected": True, "engagement_available": False})
        self.assertEqual(self.http_calls, [])       # not even the token refresh
        self.assertEqual(self.upserts, [])
        self.assertEqual(self.alerts, [])

    def test_not_connected_is_unchanged(self):
        self.agent._get_connection = lambda cid: {}
        self.assertEqual(self.agent.get_engagement_summary(27), {"connected": False})

    def test_publishing_path_does_not_touch_the_flag(self):
        # init_inbox_upload / get_post_status are video.publish calls: they
        # must keep going out while engagement reads are off
        self.service.init_inbox_upload("acc", video_size=10, chunk_size=10, total_chunk_count=1)
        self.service.get_post_status("acc", "pub-1")
        self.assertEqual([url for _m, url in self.http_calls],
                         [f"{self.service.API_BASE}/post/publish/inbox/video/init/",
                          f"{self.service.API_BASE}/post/publish/status/fetch/"])


class EngagementOn(TikTokCase):
    """The code kept behind the flag still works once it is flipped."""

    def setUp(self):
        super().setUp()
        self.service.ENGAGEMENT_READS_ENABLED = True

    def tearDown(self):
        self.service.ENGAGEMENT_READS_ENABLED = False

    def test_list_videos_goes_out(self):
        self.service.list_videos("acc")
        self.assertEqual(self.http_calls, [("POST", f"{self.service.API_BASE}/video/list/")])

    def test_summary_reads_counts(self):
        result = self.agent.get_engagement_summary(27)
        self.assertTrue(result["connected"])
        self.assertEqual(result["videos"], 0)
        self.assertNotIn("engagement_available", result)


class StatusLayers(unittest.TestCase):
    def setUp(self):
        self.saved = sys.modules.get("agents.tiktok_content_agent")
        sys.modules["agents.tiktok_content_agent"] = _module(
            "agents.tiktok_content_agent",
            get_engagement_summary=lambda cid: {"connected": True, "engagement_available": False})
        from core import performance
        self.performance = performance
        performance._local_cache.clear()

    def tearDown(self):
        self.performance._local_cache.clear()
        if self.saved is None:
            sys.modules.pop("agents.tiktok_content_agent", None)
        else:
            sys.modules["agents.tiktok_content_agent"] = self.saved

    def test_performance_card_says_not_available_yet(self):
        card = self.performance._engagement_card(27, "tiktok", {"tiktok"})
        self.assertEqual(card["status"], "connected_no_data")
        self.assertEqual(card["note"], "engagement_counts_not_available_yet")
        self.assertNotIn("reason", card)            # never sync_failed

    def test_client_state_carries_the_note(self):
        from core import client_state
        card = self.performance._engagement_card(27, "tiktok", {"tiktok"})
        saved = self.performance.channel_statuses
        self.performance.channel_statuses = lambda cid: {"plan_resolved": True,
                                                         "channels": {"tiktok": card}}
        try:
            out = client_state._channels(27)
        finally:
            self.performance.channel_statuses = saved
        self.assertEqual(out["by_channel"]["tiktok"]["note"], "engagement_counts_not_available_yet")

    def test_dashboard_has_the_note_text(self):
        with open(os.path.join(ROOT, "dashboard", "client", "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn("note_engagement_counts_not_available_yet:", html)


# ─── Public landing page: the platform-review checklist ──────────────────────

# Zero-width, bidi-control, word-joiner and BOM characters, plus a raw
# no-break space (&nbsp; entities are fine — they are visible markup)
INVISIBLE = re.compile("[ ​-‏‪-‮⁠-⁤﻿]")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class LandingPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _read("dashboard", "landing", "index.html")
        start = cls.html.index("const I18N = {")
        cls.table = cls.html[start:cls.html.index("\n};", start)]
        positions = [(m.group(1), m.start()) for m in re.finditer(r"^  (\w+): \{", cls.table, re.M)]
        cls.entries = {key: cls.table[pos:(positions[i + 1][1] if i + 1 < len(positions) else None)]
                       for i, (key, pos) in enumerate(positions)}

    def test_no_invisible_characters(self):
        for parts in (("dashboard", "landing", "index.html"), ("dashboard", "assets", "i18n.js")):
            found = INVISIBLE.findall(_read(*parts))
            self.assertEqual(found, [], "/".join(parts))

    def test_every_marked_string_exists_with_english(self):
        used = set(re.findall(r'data-i18n(?:-html)?="(\w+)"', self.html))
        self.assertTrue(used)
        for key in sorted(used):
            self.assertIn(key, self.entries, key)
            self.assertIn(" en:", self.entries[key].replace("\n    en:", " en:"), key)

    def test_footer_links_terms_and_privacy_in_every_language(self):
        footer = self.entries["footer_text"]
        for lang in ("he", "en", "fr", "ar", "ru"):
            line = re.search(rf"^    {lang}:'(.*)'", footer, re.M).group(1)
            self.assertIn('href="/terms/"', line, lang)
            self.assertIn('href="/privacy/he/"' if lang == "he" else 'href="/privacy/"', line, lang)
            self.assertIn("johnny_support@uallak.com", line, lang)

    def test_static_footer_and_contact(self):
        footer = re.search(r"<footer[^>]*>(.*?)</footer>", self.html, re.S).group(1)
        self.assertIn('href="/terms/"', footer)
        self.assertIn('href="/privacy/he/"', footer)
        self.assertIn('href="mailto:johnny_support@uallak.com"', self.html)

    def test_title_and_description_name_the_product(self):
        title = re.search(r"<title>(.*?)</title>", self.html).group(1)
        self.assertTrue(title.startswith("uallak"), title)
        description = re.search(r'<meta name="description" content="([^"]+)"', self.html).group(1)
        self.assertIn("uallak", description)
        self.assertIn("_meta_description", self.entries)

    def test_services_pricing_contact_sections_exist(self):
        for section in ('id="services"', 'id="pricing"', 'id="contact"'):
            self.assertIn(section, self.html)

    def test_internal_links_point_at_served_pages(self):
        served = {"/", "/chat/", "/login", "/terms/", "/privacy/", "/privacy/he/"}
        for path in set(re.findall(r'''(?:href=|uallakGo\()["'](/[^"'#?]*)''', self.html)):
            if path.startswith("/assets/"):
                self.assertTrue(os.path.exists(os.path.join(ROOT, "dashboard", *path.split("/")[1:])), path)
            else:
                self.assertIn(path, served, path)

    def test_tiktok_is_never_described_as_paid_ads_or_auto_posting(self):
        en = self.entries["hero_purpose"] + self.entries["auto_3_body"] + self.entries["svc_3_body"]
        self.assertNotIn("Google, Meta and TikTok", en)
        self.assertNotIn("Google, Meta, and TikTok", en)
        self.assertIn("never posts on your behalf", self.entries["svc_3_body"])


if __name__ == "__main__":
    unittest.main()
