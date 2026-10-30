"""Google verification ROUND 2, part two: Analytics (analytics.readonly) and
the client's own Drive (drive.file) — both behind flags that are OFF by
default (docs/GOOGLE-ROUND2.md).

- AnalyticsFlagOff / DriveFlagOff: nothing round-2 happens with the flags
  unset — no consent URL, no HTTP call, no traffic channel, every file stays
  in our Shared Drive and the seam touches nothing.
- TrafficComparison: the comparison logic (code, never a model): "no data
  yet" is not zero, a trend only when both periods are fully recorded.
- AnalyticsConsent: the consent asks analytics.readonly only; the reads are
  the two documented read calls.
- StorageSeam: flag off = Shared Drive + service account, flag on and
  connected + flipped = the client's Drive + their grant; every known Drive
  caller goes through drive_storage; nothing else reads DRIVE_MEDIA_FOLDER_ID.
- Migration: copy → verify (size + md5) → flip → free; never a delete
  before a verification, a bad copy blocks the flip.

Stdlib only. Run from tests/:  python test_round2_analytics_drive.py -v
"""
import ast
import contextvars
import hashlib
import os
import re
import sys
import types
import unittest
from datetime import date
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FLAGS = ("ANALYTICS_ENABLED", "DRIVE_FILE_ENABLED")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8-sig") as f:
        return f.read()


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


class Stubs:
    """sys.modules swaps, undone in reverse order."""

    def __init__(self):
        self.saved = []

    def put(self, name, module):
        self.saved.append((name, sys.modules.get(name)))
        sys.modules[name] = module
        if "." in name:
            parent, attr = name.rsplit(".", 1)
            if parent in sys.modules:
                self.saved.append((f"{parent}:{attr}", getattr(sys.modules[parent], attr, None)))
                setattr(sys.modules[parent], attr, module)

    def undo(self):
        for name, value in reversed(self.saved):
            if ":" in name:
                parent, attr = name.split(":")
                if value is None:
                    if hasattr(sys.modules.get(parent), attr):
                        delattr(sys.modules[parent], attr)
                else:
                    setattr(sys.modules[parent], attr, value)
            elif value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
        self.saved.clear()


class FlagEnv(unittest.TestCase):
    def setUp(self):
        self._saved_env = {f: os.environ.pop(f, None) for f in FLAGS}

    def tearDown(self):
        for flag, value in self._saved_env.items():
            os.environ.pop(flag, None)
            if value is not None:
                os.environ[flag] = value

    def on(self, *flags):
        for flag in flags or FLAGS:
            os.environ[flag] = "true"


# ─── Analytics service with HTTP recorded ────────────────────────────────────

HTTP = []


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body, self.text = status, body or {}, str(body)

    def json(self):
        return self._body


def _http(method):
    def run(url, **kw):
        HTTP.append((method, url, kw))
        if url.endswith("/accountSummaries"):
            return Resp(200, {"accountSummaries": [
                {"displayName": "Shin Sekai", "propertySummaries": [
                    {"property": "properties/111", "displayName": "shinsekai.co.il"}]},
                {"displayName": "Other", "propertySummaries": [
                    {"property": "properties/222", "displayName": "other site"}]}]})
        if url.endswith(":runReport"):
            return Resp(200, {"rows": []})
        return Resp(404, {})
    return run


class AnalyticsBase(FlagEnv):
    @classmethod
    def setUpClass(cls):
        cls.stubs = Stubs()
        cls.stubs.put("httpx", _module("httpx", get=_http("GET"), post=_http("POST")))
        cls.stubs.put("agents.keys_agent", _module("agents.keys_agent", get_key=lambda k: "cid-" + k))
        cls.stubs.put("core.google_ads_service", _module(
            "core.google_ads_service", OAUTH_CONSENT_URL="https://accounts.google.com/o/oauth2/v2/auth",
            _access_token=lambda rt: "at", exchange_code=lambda code, redirect: {"refresh_token": "rt"}))
        for name in ("core.analytics_service", "core.drive_file_service"):
            sys.modules.pop(name, None)
        from core import analytics_service, drive_file_service
        cls.ga, cls.dfs = analytics_service, drive_file_service

    @classmethod
    def tearDownClass(cls):
        cls.stubs.undo()

    def setUp(self):
        super().setUp()
        HTTP.clear()


class AnalyticsFlagOff(AnalyticsBase):
    def test_flags_default_off(self):
        from core import round2_flags
        self.assertFalse(round2_flags.analytics_enabled())
        self.assertFalse(round2_flags.drive_file_enabled())

    def test_no_consent_url_and_no_api_call(self):
        with self.assertRaises(self.ga.AnalyticsDisabled):
            self.ga.build_consent_url("state")
        with self.assertRaises(self.ga.AnalyticsDisabled):
            self.ga.exchange_code("code")
        with self.assertRaises(self.ga.AnalyticsDisabled):
            self.ga.list_properties("rt")
        with self.assertRaises(self.ga.AnalyticsDisabled):
            self.ga.run_report("rt", "properties/1", [{"startDate": "today", "endDate": "today"}], ["sessions"])
        self.assertEqual(HTTP, [])

    def test_traffic_reader_reads_nothing(self):
        from core import site_traffic
        saved = site_traffic._row
        site_traffic._row = lambda cid: (_ for _ in ()).throw(AssertionError("read with the flag off"))
        try:
            self.assertEqual(site_traffic.traffic(27), {"enabled": False, "connected": False})
        finally:
            site_traffic._row = saved
        self.assertEqual(site_traffic.briefing_fact({"enabled": False}), None)
        self.assertEqual(site_traffic.for_agent({"enabled": False})["why"], "not_enabled")

    def test_flag_gates_in_every_reader(self):
        # Off = the section, the channel and the briefing fact are never even built
        self.assertRegex(_read("core", "client_state.py"),
                         r"if analytics_enabled\(\):\n\s+take\(\"website_traffic\"")
        self.assertIn("if analytics_enabled():\n        channels = CHANNELS + (TRAFFIC_CHANNEL,)",
                      _read("core", "performance.py"))
        self.assertIn('facts["site_traffic"] = None', _read("agents", "engagement_agent.py"))


class PerformanceChannel(FlagEnv):
    """channel_statuses with stubbed reads: the traffic channel exists only
    with the flag on, and is the ONE resolver's output."""

    def setUp(self):
        super().setUp()
        self.stubs = Stubs()
        self.accounts = []
        self.stubs.put("agents.client_agent", _module("agents.client_agent",
                                                      get_accounts=lambda cid: self.accounts))
        self.stubs.put("core.client_journey", _module("core.client_journey", required_connections=lambda cid: {
            "steps": [{"key": "website"}], "services": [], "resolved": True}))
        sys.modules.pop("core.performance", None)
        from core import performance, site_traffic
        self.perf, self.st = performance, site_traffic
        self._saved_traffic = site_traffic.traffic

    def tearDown(self):
        self.st.traffic = self._saved_traffic
        self.stubs.undo()
        super().tearDown()

    def statuses(self):
        organic = {"tracking_connected": False, "rows": [], "summary": {}}
        leads = {"rows": [], "by_channel": True}
        saved = self.perf._tracking_card
        self.perf._tracking_card = lambda cid, platforms: self.perf._card("not_connected")
        try:
            return self.perf.channel_statuses(27, _organic=organic, _leads=leads)["channels"]
        finally:
            self.perf._tracking_card = saved

    def test_flag_off_no_channel(self):
        self.st.traffic = lambda cid: (_ for _ in ()).throw(AssertionError("read with the flag off"))
        self.assertNotIn("website_traffic", self.statuses())

    def test_flag_on_not_connected_points_at_the_card(self):
        self.on("ANALYTICS_ENABLED")
        self.st.traffic = lambda cid: {"enabled": True, "connected": False}
        card = self.statuses()["website_traffic"]
        self.assertEqual(card, {"status": "not_connected", "connect": "gaBtn"})

    def test_no_session_ever_is_no_data_not_zero(self):
        self.on("ANALYTICS_ENABLED")
        self.accounts = [{"platform": "google_analytics", "status": "active", "account_id": "properties/1"}]
        self.st.traffic = lambda cid: {"enabled": True, "connected": True, "first_data_date": None,
                                       "pulled_at": "t"}
        card = self.statuses()["website_traffic"]
        self.assertEqual(card["status"], "connected_no_data")
        self.assertNotIn("key_numbers", card)

    def test_active_and_attention_states(self):
        self.on("ANALYTICS_ENABLED")
        self.accounts = [{"platform": "google_analytics", "status": "active", "account_id": "properties/1"}]
        figures = self.st.build(date(2026, 10, 31), date(2026, 1, 1),
                                {"cur7": {"activeUsers": 70, "sessions": 90}, "prev7": {"activeUsers": 50, "sessions": 60}},
                                {"activeUsers": 4, "sessions": 5}, [])
        self.st.traffic = lambda cid: {"enabled": True, "connected": True, "pulled_at": "t", **figures}
        card = self.statuses()["website_traffic"]
        self.assertEqual(card["status"], "active")
        self.assertEqual(card["key_numbers"]["visitors_today"], 4)
        self.assertEqual(card["key_numbers"]["change_7_days"], {"direction": "up", "change_pct": 40})
        self.st.traffic = lambda cid: {"enabled": True, "connected": True, "choose_property": True}
        self.assertEqual(self.statuses()["website_traffic"]["reason"], "choose_property")
        self.st.traffic = lambda cid: {"enabled": True, "connected": True, "error": True, "reason": "reconnect"}
        self.assertEqual(self.statuses()["website_traffic"]["reason"], "reconnect")


# ─── The comparison logic ────────────────────────────────────────────────────

class TrafficComparison(unittest.TestCase):
    TODAY = date(2026, 10, 31)          # yesterday = 2026-10-30

    @classmethod
    def setUpClass(cls):
        from core import site_traffic
        cls.st = site_traffic

    def test_compare(self):
        c = self.st.compare
        self.assertEqual(c(120, 100), {"direction": "up", "change_pct": 20})
        self.assertEqual(c(75, 100), {"direction": "down", "change_pct": -25})
        self.assertEqual(c(10, 10), {"direction": "same", "change_pct": 0})
        self.assertEqual(c(0, 0), {"direction": "same", "change_pct": 0})
        self.assertEqual(c(5, 0), {"direction": "up", "change_pct": None})   # no honest % from zero
        self.assertIsNone(c(None, 10))
        self.assertIsNone(c(10, None))

    def test_no_data_is_not_zero(self):
        # The property recorded its first session 3 days ago: the 7-day
        # previous period lies before that — None ("—"), not 0
        figures = self.st.build(self.TODAY, date(2026, 10, 28), {"cur7": {"activeUsers": 9, "sessions": 11}},
                                {"activeUsers": 0, "sessions": 0}, [])
        week = figures["windows"][0]
        self.assertEqual(week["current"]["users"], 9)
        self.assertFalse(week["current"]["complete"])
        self.assertIsNone(week["previous"])
        self.assertIsNone(week["change"])                       # no trend claimed
        self.assertEqual(figures["today"]["users"], 0)          # a real zero: inside the property's life

    def test_zero_visitors_inside_the_property_life_is_zero(self):
        figures = self.st.build(self.TODAY, date(2026, 1, 1), {}, {}, [])
        week = figures["windows"][0]
        self.assertEqual((week["current"]["users"], week["previous"]["users"]), (0, 0))
        self.assertEqual(week["change"]["users"], {"direction": "same", "change_pct": 0})

    def test_never_any_session_means_no_figures_at_all(self):
        figures = self.st.build(self.TODAY, None, {}, {}, [])
        self.assertIsNone(figures["first_data_date"])
        self.assertTrue(all(w["current"] is None and w["previous"] is None for w in figures["windows"]))
        self.assertIsNone(figures["today"])
        self.assertIsNone(figures["sources"])

    def test_windows_and_previous_periods_are_adjacent_and_equal(self):
        figures = self.st.build(self.TODAY, date(2025, 1, 1), {}, {}, [])
        for w in figures["windows"]:
            cur, prev = w["current"], w["previous"]
            self.assertEqual(cur["to"], "2026-10-30")
            span = (date.fromisoformat(cur["to"]) - date.fromisoformat(cur["from"])).days + 1
            self.assertEqual(span, w["days"])
            self.assertEqual((date.fromisoformat(cur["from"]) - date.fromisoformat(prev["to"])).days, 1)
            self.assertEqual((date.fromisoformat(prev["to"]) - date.fromisoformat(prev["from"])).days + 1, w["days"])

    def test_report_ranges_match_the_windows(self):
        # GA4 relative dates: last N days end yesterday, previous N before them
        names = {r["name"]: (r["startDate"], r["endDate"]) for r in self.st.REQUEST_A + self.st.REQUEST_B}
        self.assertEqual(names["cur7"], ("7daysAgo", "yesterday"))
        self.assertEqual(names["prev7"], ("14daysAgo", "8daysAgo"))
        self.assertEqual(names["cur30"], ("30daysAgo", "yesterday"))
        self.assertEqual(names["prev30"], ("60daysAgo", "31daysAgo"))
        self.assertEqual(names["cur90"], ("90daysAgo", "yesterday"))
        self.assertEqual(names["prev90"], ("180daysAgo", "91daysAgo"))
        self.assertEqual(names["week_ago"], ("8daysAgo", "8daysAgo"))
        self.assertTrue(all(len(req) <= 4 for req in (self.st.REQUEST_A, self.st.REQUEST_B)))

    def test_parse_named_ranges(self):
        report = {"rows": [{"dimensionValues": [{"value": "cur7"}], "metricValues": [{"value": "12"}, {"value": "15"}]},
                           {"dimensionValues": [{"value": "prev7"}], "metricValues": [{"value": "8"}, {"value": "9"}]}]}
        parsed = self.st.parse_ranges(report, self.st.REQUEST_A)
        self.assertEqual(parsed["cur7"], {"activeUsers": 12.0, "sessions": 15.0})
        single = self.st.parse_ranges({"rows": [{"metricValues": [{"value": "3"}, {"value": "4"}]}]},
                                      [{"name": "today"}])
        self.assertEqual(single["today"]["activeUsers"], 3.0)

    def test_sources_grouped(self):
        grouped = self.st.group_sources([("Organic Search", 50), ("Paid Search", 20), ("Paid Social", 5),
                                         ("Direct", 30), ("Organic Social", 10), ("Referral", 4),
                                         ("Email", 2), ("Unassigned", 1)])
        self.assertEqual(grouped, {"organic": 50, "paid": 25, "direct": 30, "social": 10, "referral": 4, "other": 3})

    def test_briefing_fact_states_a_trend_only_with_both_days(self):
        t = {"enabled": True, "connected": True, "pulled_at": "t",
             **self.st.build(self.TODAY, date(2026, 1, 1),
                             {"yesterday": {"activeUsers": 30}, "week_ago": {"activeUsers": 24}}, {}, [])}
        self.assertEqual(self.st.briefing_fact(t), {"visitors_yesterday": 30, "direction": "up", "change_pct": 25,
                                                    "compared_to": "same_day_last_week",
                                                    "visitors_same_day_last_week": 24})
        # The property started 5 days ago: yesterday is real, last week's day is not
        young = {"enabled": True, "connected": True,
                 **self.st.build(self.TODAY, date(2026, 10, 26), {"yesterday": {"activeUsers": 7}}, {}, [])}
        self.assertEqual(self.st.briefing_fact(young), {"visitors_yesterday": 7})
        self.assertIsNone(self.st.briefing_fact({"enabled": True, "connected": True, "error": True}))
        self.assertIsNone(self.st.briefing_fact({"enabled": True, "connected": True, "first_data_date": None}))

    def test_agents_get_the_trend_already_computed(self):
        t = {"enabled": True, "connected": True, "pulled_at": "t",
             **self.st.build(self.TODAY, date(2026, 1, 1), {"cur7": {"activeUsers": 50}, "prev7": {"activeUsers": 100}},
                             {}, [("Direct", 3)])}
        view = self.st.for_agent(t)
        self.assertEqual(view["windows"]["last_7_days"]["trend"], {"direction": "down", "change_pct": -50})
        self.assertEqual(view["sessions_by_source_last_30_days"]["direct"], 3)
        self.assertEqual(self.st.for_agent({"enabled": True, "connected": True, "first_data_date": None}),
                         {"available": True, "has_data": False})

    def test_the_greeting_prompt_forbids_computing(self):
        prompt = _read("agents", "engagement_agent.py")
        self.assertIn("Mention up/down ONLY if `direction` is present", prompt)
        self.assertIn("Never\n  compute, round or infer a trend yourself", prompt)

    def test_a_failed_read_is_a_data_topic_not_prompt_text(self):
        source = _read("core", "client_state.py")
        self.assertIn('raise RuntimeError(f"traffic read failed', source)
        self.assertIn('"website_traffic": "website"', source)


class AnalyticsConsent(AnalyticsBase):
    def test_consent_asks_analytics_readonly_only(self):
        self.on("ANALYTICS_ENABLED")
        query = parse_qs(urlparse(self.ga.build_consent_url("st")).query)
        self.assertEqual(query["scope"][0].split(), ["https://www.googleapis.com/auth/analytics.readonly"])
        self.assertEqual(query["redirect_uri"][0], "https://app.uallak.com/api/oauth/analytics/callback")
        self.assertNotIn("include_granted_scopes", query)

    def test_properties_listed_from_the_clients_own_login(self):
        self.on("ANALYTICS_ENABLED")
        props = self.ga.list_properties("rt")
        self.assertEqual([p["property"] for p in props], ["properties/111", "properties/222"])
        self.assertEqual(props[0]["name"], "shinsekai.co.il")
        self.assertEqual(HTTP[0][1], "https://analyticsadmin.googleapis.com/v1beta/accountSummaries")

    def test_run_report_body(self):
        self.on("ANALYTICS_ENABLED")
        self.ga.run_report("rt", "properties/111", [{"name": "cur7", "startDate": "7daysAgo", "endDate": "yesterday"}],
                           ["activeUsers", "sessions"], keep_empty_rows=True)
        method, url, kw = HTTP[-1]
        self.assertEqual((method, url), ("POST", "https://analyticsdata.googleapis.com/v1beta/properties/111:runReport"))
        self.assertEqual(kw["json"]["metrics"], [{"name": "activeUsers"}, {"name": "sessions"}])
        self.assertTrue(kw["json"]["keepEmptyRows"])
        with self.assertRaises(ValueError):
            self.ga.run_report("rt", "123", [{}], ["sessions"])          # never a typed-in id
        with self.assertRaises(ValueError):
            self.ga.run_report("rt", "properties/1", [{}] * 5, ["sessions"])

    def test_module_has_no_write_call(self):
        source = _read("core", "analytics_service.py").split('"""', 2)[2]
        urls = set(re.findall(r'"(https://[^"]+)"', source)) - {"https://app.uallak.com"}   # our redirect host
        self.assertEqual(urls, {"https://www.googleapis.com/auth/analytics.readonly",
                                "https://analyticsadmin.googleapis.com/v1beta",
                                "https://analyticsdata.googleapis.com/v1beta"})
        self.assertEqual(re.findall(r"httpx\.(\w+)\(", source), ["get", "post"])
        self.assertIn(':runReport"', source)


class DriveConsent(AnalyticsBase):
    def test_flag_off_no_consent(self):
        with self.assertRaises(self.dfs.DriveFileDisabled):
            self.dfs.build_consent_url("state")
        with self.assertRaises(self.dfs.DriveFileDisabled):
            self.dfs.create_folders("Shin Sekai")
        self.assertEqual(HTTP, [])

    def test_consent_asks_drive_file_only(self):
        self.on("DRIVE_FILE_ENABLED")
        query = parse_qs(urlparse(self.dfs.build_consent_url("st")).query)
        self.assertEqual(query["scope"][0].split(), ["https://www.googleapis.com/auth/drive.file"])
        self.assertEqual(query["redirect_uri"][0], "https://app.uallak.com/api/oauth/google-drive/callback")
        self.assertNotIn("include_granted_scopes", query)

    def test_full_drive_is_never_requested_from_a_client(self):
        for path in ("core/drive_file_service.py", "core/analytics_service.py", "core/drive_storage.py"):
            source = _read(*path.split("/"))
            self.assertNotIn('"https://www.googleapis.com/auth/drive"', source, path)
            self.assertNotIn("drive.readonly", source, path)
        # the service account's own scope list is untouched
        self.assertIn('DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]', _read("core", "drive_service.py"))

    def test_the_client_text_says_exactly_what_drive_file_allows(self):
        html = _read("dashboard", "client", "index.html")
        line = re.search(r"^  gdrive_card_note: \{(.*)\},$", html, re.M).group(1)
        self.assertIn("רק קבצים שהמערכת עצמה יצרה", line)
        self.assertIn("ולעולם לא נוכל לקרוא שום דבר אחר", line)
        self.assertIn("can never read anything else in your Drive", line)
        for lang in ("he:", "en:", "fr:", "ar:", "ru:"):
            self.assertIn(lang, line)
        from core import ownership
        self.assertIn("ורק אותם", ownership.GROUPS["google_drive"]["access"]["he"])

    def test_folder_name_and_structure(self):
        self.assertEqual(self.dfs.folder_name("  Shin   Sekai "), "uallak — Shin Sekai")
        for name in ("images", "videos", "voice", "avatar-source", "exports", "documents"):
            self.assertIn(name, self.dfs.SUBFOLDERS)


# ─── The storage seam ────────────────────────────────────────────────────────

class StorageSeam(FlagEnv):
    @classmethod
    def setUpClass(cls):
        cls.stubs = Stubs()
        cls.stubs.put("httpx", _module("httpx", get=None, post=None, Response=object, HTTPStatusError=Exception,
                                       TransportError=Exception, Timeout=lambda **k: None))
        cls.stubs.put("agents.keys_agent", _module("agents.keys_agent", get_key=lambda k: ""))
        cls.stubs.put("core.secrets_vault", _module("core.secrets_vault",
                                                    get_client_secret=lambda cid, p, **k: f"refresh-{cid}"))
        cls.stubs.put("core.google_ads_service", _module("core.google_ads_service",
                                                         _access_token=lambda rt: f"user-token-for-{rt}"))
        sys.modules.pop("core.drive_service", None)
        import core
        if hasattr(core, "drive_service"):
            delattr(core, "drive_service")
        from core import drive_service, drive_storage
        cls.drive, cls.ds = drive_service, drive_storage
        cls._saved = (drive_storage._grant_row, drive_storage.record, drive_service._access_token)
        drive_service._access_token = lambda: "service-account-token"

    @classmethod
    def tearDownClass(cls):
        cls.ds._grant_row, cls.ds.record, cls.drive._access_token = cls._saved
        cls.stubs.undo()

    def setUp(self):
        super().setUp()
        self.ds._location_cache.clear()
        self.rows = {7: {"platform": "google_drive_file", "status": "active", "account_id": "CLIENT-ROOT-7"}}
        self.records = {7: {"root": "CLIENT-ROOT-7", "parent": "MYDRIVE-7", "pointer": "client_drive"},
                        8: {}}
        self.ds._grant_row = lambda cid: self.rows.get(cid, {})
        self.ds.record = lambda cid: self.records.get(cid, {})
        os.environ["DRIVE_MEDIA_FOLDER_ID"] = "SHARED-MEDIA-ROOT"

    def tearDown(self):
        os.environ.pop("DRIVE_MEDIA_FOLDER_ID", None)
        super().tearDown()

    def token(self):
        return self.drive._headers()["Authorization"]

    def test_flag_off_is_shared_drive_and_reads_nothing(self):
        self.ds._grant_row = lambda cid: (_ for _ in ()).throw(AssertionError("read with the flag off"))
        self.assertEqual(self.ds.location(7), {"kind": "shared_drive", "parent": "SHARED-MEDIA-ROOT"})
        with self.ds.scope(7):
            self.assertEqual(self.token(), "Bearer service-account-token")

    def test_flag_on_connected_and_flipped_is_the_clients_drive(self):
        self.on("DRIVE_FILE_ENABLED")
        where = self.ds.location(7)
        self.assertEqual((where["kind"], where["root"], where["parent"]), ("client_drive", "CLIENT-ROOT-7", "MYDRIVE-7"))
        with self.ds.scope(7):
            self.assertEqual(self.token(), "Bearer user-token-for-refresh-7")
        self.assertEqual(self.token(), "Bearer service-account-token")    # scope ended

    def test_flag_on_but_not_connected_or_not_flipped_stays_shared(self):
        self.on("DRIVE_FILE_ENABLED")
        self.assertEqual(self.ds.location(8)["kind"], "shared_drive")          # not connected
        self.records[7]["pointer"] = "shared_drive"                             # migration not done
        self.assertEqual(self.ds.location(7)["kind"], "shared_drive")
        self.ds._location_cache.clear()
        self.records[7] = {"root": "OLD-ROOT", "pointer": "client_drive"}       # record of another grant
        self.assertEqual(self.ds.location(7)["kind"], "shared_drive")
        self.ds._location_cache.clear()
        self.ds._grant_row = lambda cid: (_ for _ in ()).throw(RuntimeError("db down"))
        self.assertEqual(self.ds.location(7)["kind"], "shared_drive")          # any doubt → today

    def test_scoped_decorator_and_nesting(self):
        self.on("DRIVE_FILE_ENABLED")
        seen = []

        @self.ds.scoped
        def write(client_id, name):
            seen.append((client_id, self.token()))
            if client_id == 7:
                write(8, "nested")       # another client inside: their own location
                seen.append(("back", self.token()))
        write(7, "x")
        self.assertEqual(seen, [(7, "Bearer user-token-for-refresh-7"), (8, "Bearer service-account-token"),
                                ("back", "Bearer user-token-for-refresh-7")])
        with self.assertRaises(TypeError):
            self.ds.scoped(lambda folder: None)

    def test_only_drive_storage_reads_the_shared_root_setting(self):
        offenders = []
        for folder in ("core", "agents"):
            for name in os.listdir(os.path.join(ROOT, folder)):
                if not name.endswith(".py") or name in ("drive_storage.py", "keys_agent.py"):
                    continue
                code = _read(folder, name)
                if re.search(r"""(environ\.get|environ\[|get_key)\(?\s*["']DRIVE_MEDIA_FOLDER_ID""", code):
                    offenders.append(f"{folder}/{name}")
        self.assertEqual(offenders, [])

    def test_every_drive_caller_goes_through_the_seam(self):
        must = {
            "agents/media_agent.py": ["ensure_client_media_root", "_subfolder", "get_client_media_link",
                                      "drive_self_test", "sync_website_media_folders", "prepare_for_publishing",
                                      "generate_image", "generate_video", "create_filming_kit"],
            "core/drive_mirror.py": ["client_root_id", "list_folder", "thumbnail"],
            "core/drive_download.py": ["_verify_in_client_tree", "plan", "open_stream"],
            "core/chat_uploads.py": ["_folder_id", "begin", "send", "status"],
            "core/voice_recordings.py": ["_folder", "upload", "list_recordings", "delete"],
            "agents/avatar_agent.py": ["request_source_kit", "_source_files", "create_avatar", "create_voice_clone",
                                       "generate_avatar_video"],
            "core/avatar_video.py": ["_character_image", "_audio_from_recording", "_audio_from_clone"],
            "agents/content_docs_agent.py": ["deliver_doc"],
            "agents/youtube_content_agent.py": ["publish"],
            "agents/tiktok_content_agent.py": ["publish"],
            "core/agent_actions.py": ["_v_media_video", "_media_name"],
            "core/verification_demo.py": ["_demo_video_file"],
        }
        for path, names in must.items():
            tree = ast.parse(_read(*path.split("/")))
            decorated = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)
                         and any(ast.unparse(d) == "drive_storage.scoped" for d in n.decorator_list)}
            self.assertEqual(set(names) - decorated, set(), path)
        exports = _read("core", "api_server.py")
        helper = exports[exports.index("def _export_doc_to_drive("):exports.index("@app.post(\"/api/client/export/{dataset}/google-doc\")")]
        self.assertIn("with drive_storage.scope(client_id):", helper)
        self.assertEqual(exports.count("_subfolder(client_id, \"exports\")"), 1)   # only inside the helper

    def test_a_scoped_module_calls_drive_only_inside_scoped_functions(self):
        # chat_uploads / voice_recordings: every function that touches
        # drive_service is one of the scoped ones (a new unscoped reader fails)
        for path in ("core/chat_uploads.py", "core/voice_recordings.py", "core/drive_mirror.py"):
            source = _read(*path.split("/"))
            tree = ast.parse(source)
            for node in tree.body:
                if not isinstance(node, ast.FunctionDef):
                    continue
                body = ast.get_source_segment(source, node)
                touches = re.search(r"\bdrive\.(start_resumable|put_chunk|resumable_status|upload_bytes|list_files|"
                                    r"trash_file|list_children|get_meta|fetch_thumbnail)\(", body)
                if touches:
                    decorated = any(ast.unparse(d) == "drive_storage.scoped" for d in node.decorator_list)
                    called_from_scoped = node.name in ("_assert_in_client_tree", "shared_client_root")
                    self.assertTrue(decorated or called_from_scoped, f"{path}:{node.name}")


# ─── The migration ───────────────────────────────────────────────────────────

class FakeDrive(types.ModuleType):
    """Two sides — our Shared Drive ("sa") and the client's Drive ("client")
    — told apart by the auth override, exactly like the real _headers()."""
    FOLDER_MIME = "application/vnd.google-apps.folder"

    class ResumableSessionGone(RuntimeError):
        pass

    def __init__(self):
        super().__init__("core.drive_service")
        self._auth_override = contextvars.ContextVar("fake_override", default=None)
        self.files, self.sessions, self.n = {}, {}, 0

    def side(self):
        return "client" if self._auth_override.get() else "sa"

    def _new(self, **f):
        self.n += 1
        fid = f"{f['side']}-{self.n}"
        self.files[fid] = {"trashed": False, "appProperties": {}, "content": None, **f}
        return fid

    def _see(self, fid):
        f = self.files.get(fid)
        if not f or f["side"] != self.side():
            raise PermissionError(f"{self.side()} cannot see {fid}")
        return f

    def list_children(self, folder, page_token="", page_size=40, fields=""):
        self._see(folder)
        out = []
        for fid, f in self.files.items():
            if f["parent"] == folder and not f["trashed"]:
                item = {"id": fid, "name": f["name"], "mimeType": f["mimeType"]}
                if f["content"] is not None and not f["mimeType"].startswith("application/vnd.google-apps."):
                    item.update(size=str(len(f["content"])), md5Checksum=hashlib.md5(f["content"]).hexdigest())
                out.append(item)
        return {"files": out, "next_page_token": ""}

    def get_meta(self, fid, fields=""):
        f = self._see(fid)
        meta = {"id": fid, "mimeType": f["mimeType"], "trashed": f["trashed"], "appProperties": f["appProperties"]}
        if f["content"] is not None and not f["mimeType"].startswith("application/vnd.google-apps."):
            meta.update(size=str(len(f["content"])), md5Checksum=hashlib.md5(f["content"]).hexdigest())
        return meta

    def ensure_folder(self, name, parent):
        self._see(parent)
        for fid, f in self.files.items():
            if f["parent"] == parent and f["name"] == name and f["mimeType"] == self.FOLDER_MIME:
                return fid
        return self._new(side=self.side(), name=name, parent=parent, mimeType=self.FOLDER_MIME)

    def read_range(self, fid, start, length):
        return self._see(fid)["content"][start:start + length]

    def start_resumable(self, folder, name, mime, total, props=None, upload_mime=""):
        self._see(folder)
        uri = f"https://www.googleapis.com/upload/drive/v3/files?s={len(self.sessions)}"
        self.sessions[uri] = {"side": self.side(), "folder": folder, "name": name, "mime": mime,
                              "total": total, "props": props or {}, "buf": b""}
        return uri

    def put_chunk(self, uri, data, offset, total):
        s = self.sessions[uri]
        assert s["side"] == self.side(), "chunk sent with another account's credentials"
        assert offset == len(s["buf"])
        s["buf"] += data
        if len(s["buf"]) < s["total"]:
            return {"done": False, "next_offset": len(s["buf"])}
        fid = self._new(side=s["side"], name=s["name"], parent=s["folder"], mimeType=s["mime"],
                        content=s["buf"], appProperties=dict(s["props"]))
        return {"done": True, "file": {"id": fid}}

    def upload_bytes(self, folder, name, content, mime, props=None):
        self._see(folder)
        return {"id": self._new(side=self.side(), name=name, parent=folder, mimeType=mime, content=content,
                                appProperties=dict(props or {}))}

    def open_download_stream(self, fid, export_mime=""):
        content = self._see(fid)["content"]
        closer = types.SimpleNamespace(close=lambda: None)
        return closer, types.SimpleNamespace(iter_bytes=lambda: iter([content]), close=lambda: None)

    def trash_file(self, fid):
        self._see(fid)["trashed"] = True


class Migration(FlagEnv):
    def setUp(self):
        super().setUp()
        self.on("DRIVE_FILE_ENABLED")
        self.fake = FakeDrive()
        self.stubs = Stubs()
        self.stubs.put("core.drive_service", self.fake)
        from core import drive_migration, drive_storage
        self.mig, self.ds = drive_migration, drive_storage
        # our Shared Drive: client-27 folder with nested files
        f = self.fake
        self.src_root = f._new(side="sa", name="client-27 — Shin", parent="SHARED", mimeType=f.FOLDER_MIME)
        videos = f._new(side="sa", name="videos", parent=self.src_root, mimeType=f.FOLDER_MIME)
        tiktok = f._new(side="sa", name="tiktok", parent=videos, mimeType=f.FOLDER_MIME)
        self.video = f._new(side="sa", name="clip.mp4", parent=tiktok, mimeType="video/mp4", content=b"V" * 23)
        self.image = f._new(side="sa", name="a.png", parent=self.src_root, mimeType="image/png", content=b"PNG!")
        self.doc = f._new(side="sa", name="brief", parent=self.src_root,
                          mimeType="application/vnd.google-apps.document", content=b"docx-bytes")
        self.dst_root = f._new(side="client", name="uallak — Shin", parent="MYDRIVE", mimeType=f.FOLDER_MIME)
        self.rec = {"root": self.dst_root, "parent": "MYDRIVE", "pointer": "shared_drive"}
        self._saved = (drive_storage.record, drive_storage.save_record, drive_storage._client_token_fn,
                       drive_migration._source_root, drive_migration.CHUNK_BYTES)
        drive_storage.record = lambda cid: self.rec
        drive_storage.save_record = lambda cid, value: self.rec.update(value)
        drive_storage._client_token_fn = lambda cid: (lambda: "client-token")
        drive_migration._source_root = lambda cid: self.src_root
        drive_migration.CHUNK_BYTES = 10      # several chunks per file

    def tearDown(self):
        (self.ds.record, self.ds.save_record, self.ds._client_token_fn,
         self.mig._source_root, self.mig.CHUNK_BYTES) = self._saved
        self.stubs.undo()
        super().tearDown()

    def copies(self):
        return {fid: f for fid, f in self.fake.files.items() if f["side"] == "client" and f["mimeType"] != self.fake.FOLDER_MIME}

    def test_copy_keeps_paths_and_bytes_and_is_idempotent(self):
        result = self.mig.copy(27)
        self.assertEqual((result["copied_now"], result["remaining"], result["failed"]), (3, 0, []))
        copies = self.copies()
        clip = next(f for f in copies.values() if f["name"] == "clip.mp4")
        self.assertEqual(clip["content"], b"V" * 23)
        self.assertEqual(clip["appProperties"]["uallak_src_id"], self.video)
        parent = self.fake.files[clip["parent"]]
        self.assertEqual((parent["name"], self.fake.files[parent["parent"]]["name"]), ("tiktok", "videos"))
        self.assertEqual(self.mig.copy(27)["copied_now"], 0)          # nothing copied twice
        self.assertEqual(len(self.copies()), 3)
        self.assertFalse(any(f["trashed"] for f in self.fake.files.values()))   # copy never deletes

    def test_verify_flip_free_happy_path(self):
        self.mig.copy(27)
        self.assertTrue(self.mig.verify(27)["ok"])
        self.assertEqual(self.mig.flip(27)["checked"], 3)
        self.assertEqual(self.rec["pointer"], "client_drive")
        with self.assertRaises(self.mig.MigrationRefused) as e:
            self.mig.free_shared(27, "yes")
        self.assertEqual(e.exception.code, "confirm_required")
        self.assertFalse(self.fake.files[self.src_root]["trashed"])
        self.mig.free_shared(27, "FREE 27")
        self.assertTrue(self.fake.files[self.src_root]["trashed"])
        self.assertIn("freed_at", self.rec["migration"])

    def test_a_corrupt_copy_blocks_the_flip_and_the_free(self):
        self.mig.copy(27)
        clip = next(f for f in self.copies().values() if f["name"] == "clip.mp4")
        clip["content"] = b"V" * 22 + b"X"                 # same size, different bytes
        result = self.mig.verify(27)
        self.assertFalse(result["ok"])
        self.assertEqual(result["problems"][0]["reason"], "checksum_differs")
        with self.assertRaises(self.mig.MigrationRefused) as e:
            self.mig.flip(27)
        self.assertEqual(e.exception.code, "not_verified")
        self.assertEqual(self.rec["pointer"], "shared_drive")
        self.rec["pointer"] = "client_drive"               # even if someone flipped by hand
        with self.assertRaises(self.mig.MigrationRefused):
            self.mig.free_shared(27, "FREE 27")
        self.assertFalse(self.fake.files[self.src_root]["trashed"])

    def test_a_file_added_after_the_copy_fails_verification(self):
        self.mig.copy(27)
        self.fake._new(side="sa", name="late.jpg", parent=self.src_root, mimeType="image/jpeg", content=b"late")
        result = self.mig.verify(27)
        self.assertFalse(result["ok"])
        self.assertEqual(result["problems"][0]["reason"], "missing")
        self.assertEqual(self.mig.copy(27)["copied_now"], 1)
        self.assertTrue(self.mig.verify(27)["ok"])

    def test_free_before_the_flip_is_refused(self):
        self.mig.copy(27)
        with self.assertRaises(self.mig.MigrationRefused) as e:
            self.mig.free_shared(27, "FREE 27")
        self.assertEqual(e.exception.code, "not_flipped")
        self.assertFalse(self.fake.files[self.src_root]["trashed"])

    def test_a_trashed_copy_fails(self):
        self.mig.copy(27)
        next(iter(self.copies().values()))["trashed"] = True
        self.assertFalse(self.mig.verify(27)["ok"])

    def test_compare_copy(self):
        c = self.mig.compare_copy
        src = {"mimeType": "video/mp4", "size": "10", "md5Checksum": "abc"}
        self.assertEqual(c(src, {"mimeType": "video/mp4", "size": "10", "md5Checksum": "abc"}), "")
        self.assertEqual(c(src, {}), "missing")
        self.assertEqual(c(src, {"size": "9", "md5Checksum": "abc"}), "size_differs")
        self.assertEqual(c(src, {"size": "10", "md5Checksum": "abd"}), "checksum_differs")
        self.assertEqual(c({"mimeType": "video/mp4", "size": "10"}, {"size": "10"}), "checksum_differs")  # no md5 = no proof
        native = {"mimeType": "application/vnd.google-apps.document"}
        self.assertEqual(c(native, {"mimeType": "application/vnd.google-apps.document"}), "")
        self.assertEqual(c(native, {"mimeType": "application/pdf"}), "type_differs")

    def test_not_connected_is_refused(self):
        self.rec.clear()
        with self.assertRaises(self.mig.MigrationRefused) as e:
            self.mig.copy(27)
        self.assertEqual(e.exception.code, "not_connected")


class OwnershipReflectsWhereFilesLive(unittest.TestCase):
    def test_asset_follows_the_location(self):
        from core import drive_storage, ownership
        saved = (ownership._asset_activity, ownership._count_landing_pages, ownership._count_leads,
                 drive_storage.location)
        ownership._asset_activity = lambda cid: [{"action_type": "media_folder_created"}]
        ownership._count_landing_pages = ownership._count_leads = lambda cid: 0
        try:
            drive_storage.location = lambda cid: {"kind": "shared_drive"}
            self.assertEqual([a["kind"] for a in ownership.assets_view(27, accounts=[])], ["drive_folder"])
            drive_storage.location = lambda cid: {"kind": "client_drive", "link": "https://drive.google.com/x"}
            assets = ownership.assets_view(27, accounts=[])
            self.assertEqual([(a["kind"], a["link"]) for a in assets], [("drive_folder_own", "https://drive.google.com/x")])
        finally:
            (ownership._asset_activity, ownership._count_landing_pages, ownership._count_leads,
             drive_storage.location) = saved
        self.assertIn("רק מסירים את הגישה", ownership.ASSET_STEPS["drive_folder_own"])

    def test_disconnect_groups_and_revoke(self):
        api = _read("core", "api_server.py")
        self.assertIn('"analytics": ["google_analytics"]', api)
        self.assertIn('"google_drive": ["google_drive_file"]', api)
        revocable = re.search(r"_REVOCABLE_GROUPS = \(([^)]*)\)", api).group(1)
        self.assertIn('"analytics"', revocable)
        self.assertIn('"google_drive"', revocable)

    def test_record_is_cleared_on_deletion_and_guarded(self):
        self.assertIn('f"drive_storage:{int(i)}"', _read("core", "client_deletion.py"))
        self.assertIn('"drive_storage:"', _read("core", "deletion_guard.py"))


if __name__ == "__main__":
    unittest.main()
