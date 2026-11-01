"""The morning briefing (agents/morning_briefing.py, engagement_agent, chat_rollover).

- quiet day: ONE line naming what is in progress, never empty or generic
- busy day: at most 3 lines, no exclamation marks, ONE approval link that
  opens the card, conversions folded in
- urgent day: urgent line first, pushed ONCE per issue (WhatsApp, else email)
- rhythm: nothing on Shabbat / chag / memorial day, short on Friday and erev chag
- one LLM call per client per day (idempotent), none when nothing is connected,
  a code-built text when the model fails
- login moment never repeats what the briefing said today
- ordering: after the 08:00 rollover, and a deferred rollover cuts AT the briefing

Stdlib only; every external seam is a fake. Run:  python tests/test_morning_briefing.py -v
"""
import io
import os
import sys
import types
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents import morning_briefing as mb   # noqa: E402
from core import chat_rollover, israel_calendar   # noqa: E402

UTC = timezone.utc
MONDAY = datetime(2026, 11, 2, 6, 35, tzinfo=UTC)        # 08:35 Israel (IST, +2)
FRIDAY = datetime(2026, 11, 6, 6, 35, tzinfo=UTC)
SATURDAY = datetime(2026, 11, 7, 6, 35, tzinfo=UTC)
ROSH_HASHANA_2 = datetime(2027, 10, 3, 5, 35, tzinfo=UTC)  # Sunday, 2nd day of the chag (IDT, +3)
YOM_HAZIKARON = datetime(2027, 5, 11, 5, 35, tzinfo=UTC)
EREV_PESACH_7 = datetime(2027, 4, 27, 5, 35, tzinfo=UTC)


# ─── A tiny in-memory Supabase ───────────────────────────────────────────────

class Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, *_a):
        return self

    def _get(self, row, col):
        if "->>" in col:
            base, key = col.split("->>")
            return str((row.get(base) or {}).get(key, ""))
        return row.get(col)

    def eq(self, col, value):
        self.rows = [r for r in self.rows if self._get(r, col) == value]
        return self

    def gte(self, col, value):
        self.rows = [r for r in self.rows if (self._get(r, col) or "") >= value]
        return self

    def in_(self, col, values):
        self.rows = [r for r in self.rows if self._get(r, col) in values]
        return self

    def order(self, col, desc=False):
        self.rows.sort(key=lambda r: self._get(r, col) or "", reverse=desc)
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    def execute(self):
        return types.SimpleNamespace(data=[dict(r) for r in self.rows])


class FakeDB:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return Query(self.tables.setdefault(name, []))


CLIENT = {"id": 27, "name": "דנה", "status": "active", "email": "dana@example.com", "phone": "0501234567",
          "manager_persona": "nissim"}


class Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.db.tables["client_accounts"] = [{"client_id": 27, "platform": "google_ads", "status": "active"}]
        self.chat, self.llm_calls, self.whatsapp, self.emails, self.alerts = [], [], [], [], []
        self.llm_answer = {"urgent_line": "", "lines": ["בוקר טוב דנה, היום ממשיכים."], "approval_line": -1}
        self.health = {}
        self.conversions = {}
        self.cards = []
        self.wa_configured = True
        self.saved = {n: getattr(mb, n) for n in (
            "_db", "_client", "_post_chat", "_log", "_conversions", "_open_cards", "_google_ads_health",
            "_llm", "_send_whatsapp", "_whatsapp_configured", "_send_email", "_alert", "_action_title",
            "_active_clients", "_now")}
        mb._db = lambda: self.db
        mb._client = lambda cid: dict(CLIENT)
        mb._active_clients = lambda: [dict(CLIENT)]
        mb._post_chat = self._post_chat
        mb._log = self._log
        mb._conversions = lambda cid: dict(self.conversions)
        mb._open_cards = lambda cid: list(self.cards)
        mb._google_ads_health = lambda cid: dict(self.health)
        mb._llm = self._llm
        mb._send_whatsapp = lambda phone, text: self.whatsapp.append(text) or True
        mb._whatsapp_configured = lambda: self.wa_configured
        mb._send_email = lambda client, text: self.emails.append(text) or True
        mb._alert = lambda issues: self.alerts.append(issues)
        mb._action_title = lambda name: {"publish_landing_page": "פרסום דף הנחיתה"}.get(name, "")

    def tearDown(self):
        for name, fn in self.saved.items():
            setattr(mb, name, fn)

    def _post_chat(self, cid, text):
        self.chat.append(text)
        return {"created_at": "2026-11-02T06:35:01.000000+00:00"}

    def _log(self, cid, action, details, result=None):
        self.db.tables.setdefault("client_activity", []).append(
            {"client_id": cid, "agent_name": mb.AGENT_NAME, "action_type": action, "details": details,
             "created_at": f"2026-11-02T06:35:0{len(self.db.tables['client_activity'])}+00:00"})

    def _llm(self, system, payload, client_id):
        self.llm_calls.append((system, payload))
        if isinstance(self.llm_answer, Exception):
            raise self.llm_answer
        return dict(self.llm_answer)

    def run_at(self, now):
        with redirect_stdout(io.StringIO()):
            return mb.run_for_client(27, now)


class QuietDay(Base):
    def test_one_line_naming_what_is_in_progress(self):
        self.llm_answer = {"urgent_line": "", "lines": ["בוקר טוב דנה, היום אנחנו ממשיכים במעקב אחרי הקמפיינים בחיפוש.",
                                                        "שורה עודפת", "ועוד אחת"], "approval_line": -1}
        out = self.run_at(MONDAY)
        self.assertTrue(out["sent"], out)
        self.assertEqual(len(self.chat), 1)
        self.assertEqual(self.chat[0].count("\n"), 0)              # ONE line
        self.assertNotIn("http", self.chat[0])
        payload = self.llm_calls[0][1]
        self.assertEqual(payload["in_progress"], ["מעקב שוטף אחרי הקמפיינים בחיפוש"])
        self.assertEqual(self.whatsapp + self.emails, [])

    def test_failed_model_still_greets_specifically(self):
        self.llm_answer = RuntimeError("model down")
        out = self.run_at(MONDAY)
        self.assertEqual(out["source"], "fallback")
        self.assertIn("מעקב שוטף אחרי הקמפיינים בחיפוש", self.chat[0])
        self.assertTrue(self.chat[0].strip())


class BusyDay(Base):
    def setUp(self):
        super().setUp()
        self.db.tables["client_leads"] = [
            {"client_id": 27, "status": "new", "source": "form", "created_at": "2026-11-01T18:00:00+00:00"},
            {"client_id": 27, "status": "contacted", "source": "phone", "created_at": "2026-11-01T20:00:00+00:00"}]
        self.db.tables["client_activity"] = [
            {"client_id": 27, "agent_name": "media_agent", "action_type": "media_image_created",
             "details": {"title": "באנר לחנוכה"}, "created_at": "2026-11-01T12:00:00+00:00"}]
        self.cards = [{"id": 41, "persona": "website", "title": "פרסום דף הנחיתה", "action": "publish_landing_page",
                       "can_decide": True},
                      {"id": 42, "persona": "general", "title": "בקשה לצוות", "action": "team_request",
                       "can_decide": False}]
        self.conversions = {mb.SEARCH_ADS: 3}
        self.llm_answer = {"urgent_line": "",
                           "lines": ["בוקר טוב דנה, מאתמול נכנסו 2 לידים והקמפיינים הביאו 3 המרות!",
                                     "אתמול הכנו בשבילך באנר לחנוכה.",
                                     "דף הנחיתה מחכה לאישור שלך https://evil.example/x", "שורה רביעית"],
                           "approval_line": 2}

    def test_three_lines_one_link_no_exclamation(self):
        self.run_at(MONDAY)
        lines = self.chat[0].split("\n")
        self.assertEqual(len(lines), 3)
        self.assertNotIn("!", self.chat[0])
        self.assertNotIn("evil.example", self.chat[0])            # model URLs are dropped
        self.assertEqual(self.chat[0].count("https://"), 1)        # ONE call to action
        self.assertTrue(lines[2].endswith("/dashboard/?approve=card:41&persona=website"))
        payload = self.llm_calls[0][1]
        self.assertEqual(payload["new_leads"], 2)
        self.assertEqual(payload["leads_waiting_for_reply"], 1)
        self.assertEqual(payload["conversions"], {mb.SEARCH_ADS: 3})   # the old sales email, folded in
        self.assertIn("באנר לחנוכה", " ".join(payload["done_by_us"]))
        self.assertEqual([w["title"] for w in payload["waiting_for_approval"]], ["פרסום דף הנחיתה"])
        self.assertEqual(payload["waiting_for_approval"][0]["kind"], "landing_page")
        self.assertNotIn("id", payload["waiting_for_approval"][0])   # the model never builds the link

    def test_friday_is_shorter(self):
        self.run_at(FRIDAY)
        self.assertEqual(len(self.chat[0].split("\n")), 2)
        self.assertEqual(self.llm_calls[0][1]["day_kind"], "short")
        self.assertIn("approve=card:41", self.chat[0])

    def test_fallback_text_keeps_the_link(self):
        self.llm_answer = ValueError("malformed")
        self.run_at(MONDAY)
        self.assertIn("approve=card:41", self.chat[0])
        self.assertIn("לידים", self.chat[0])
        self.assertLessEqual(len(self.chat[0].split("\n")), 3)

    def test_suggestion_link(self):
        self.cards = []
        self.db.tables["client_suggestions"] = [{"client_id": 27, "id": 7, "title": "תוכנית תוכן", "kind": "media_plan",
                                                 "status": "pending", "created_at": "2026-11-01T10:00:00+00:00"}]
        self.run_at(MONDAY)
        self.assertIn("/dashboard/?approve=suggestion:7", self.chat[0])


class UrgentDay(Base):
    def setUp(self):
        super().setUp()
        self.health = {"ok": False, "dead_since": "2026-11-01T22:00:00+00:00"}
        self.llm_answer = {"urgent_line": "דבר אחד דחוף: החיבור לחשבון הקמפיינים בחיפוש נפל!",
                           "lines": ["בוקר טוב דנה."], "approval_line": -1}

    def test_urgent_first_and_pushed_once(self):
        self.run_at(MONDAY)
        self.assertTrue(self.chat[0].startswith("דבר אחד דחוף"))
        self.assertEqual(len(self.whatsapp), 1)
        self.assertEqual(self.emails, [])
        self.assertNotIn("!", self.whatsapp[0])
        # the next morning: same issue, never pushed again (and not in the urgent list)
        self.chat.clear()
        with redirect_stdout(io.StringIO()):
            mb.run_for_client(27, datetime(2026, 11, 3, 6, 35, tzinfo=UTC))
        self.assertEqual(len(self.whatsapp), 1)
        self.assertEqual(self.llm_calls[-1][1]["urgent"], [])

    def test_email_when_whatsapp_unavailable(self):
        self.wa_configured = False
        self.run_at(MONDAY)
        self.assertEqual((len(self.whatsapp), len(self.emails)), (0, 1))

    def test_nothing_urgent_means_no_push(self):
        self.health = {"ok": True}
        self.run_at(MONDAY)
        self.assertEqual(self.whatsapp + self.emails, [])
        self.assertFalse(self.chat[0].startswith("דבר אחד דחוף"))


class Rhythm(Base):
    def test_nothing_on_shabbat_chag_or_memorial_day(self):
        for when, reason in ((SATURDAY, "shabbat"), (ROSH_HASHANA_2, "chag:rosh_hashana"),
                             (YOM_HAZIKARON, "memorial:yom_hazikaron")):
            out = self.run_at(when)
            self.assertEqual(out, {"sent": False, "reason": reason})
        self.assertEqual(self.llm_calls, [])
        self.assertEqual(self.chat, [])

    def test_erev_chag_is_short(self):
        self.assertEqual(mb.day_kind(chat_rollover.israel_now(EREV_PESACH_7)), ("short", "erev:pesach"))

    def test_calendar_days_derived_from_the_verified_erev_dates(self):
        info = israel_calendar.day_info
        self.assertEqual(info(date(2026, 10, 3))["rest"], "sukkot")          # Shemini Atzeret
        self.assertEqual(info(date(2027, 4, 28))["rest"], "pesach")          # 7th day of Pesach
        self.assertEqual(info(date(2027, 10, 2))["rest"], "rosh_hashana")
        self.assertEqual(info(date(2027, 10, 2))["erev"], "")                # rest wins over erev
        self.assertEqual(info(date(2027, 10, 1))["erev"], "rosh_hashana")
        self.assertEqual(info(date(2026, 11, 2)), {"shabbat": False, "friday": False, "rest": "",
                                                   "erev": "", "quiet": ""})

    def test_scheduled_after_the_rollover_sunday_to_friday(self):
        with open(os.path.join(ROOT, "scripts", "schedule_jobs.sh"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('job engagement-morning GET "30 8 * * 0-5" "/api/engagement/morning" active', src)
        self.assertIn('job chat-rollover GET "0 8 * * *" "/api/chat/rollover"', src)
        self.assertIn('retire engagement-daily', src)
        self.assertNotIn('job engagement-daily', src)


class CostAndIdempotence(Base):
    def test_one_call_per_client_per_day(self):
        self.run_at(MONDAY)
        out = self.run_at(datetime(2026, 11, 2, 7, 50, tzinfo=UTC))
        self.assertEqual(out, {"sent": False, "reason": "already_sent_today"})
        self.assertEqual(len(self.llm_calls), 1)
        self.assertEqual(len(self.chat), 1)

    def test_nothing_connected_is_skipped_without_a_call(self):
        self.db.tables["client_accounts"] = []
        self.assertEqual(self.run_at(MONDAY), {"sent": False, "reason": "nothing_connected"})
        self.assertEqual(self.llm_calls, [])

    def test_llm_call_is_single_shot_and_costed(self):
        with open(os.path.join(ROOT, "agents", "morning_briefing.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("retry_empty=False, json_retry=False", src)
        self.assertIn('COST_CATEGORY = "engagement_morning"', src)
        self.assertIn('model_routing.locked_model("manager")', src)

    def test_manager_voice_is_the_chosen_one(self):
        self.run_at(MONDAY)
        system, payload = self.llm_calls[0]
        self.assertIn("Nissim", system)
        from core import manager_identity
        self.assertIn(manager_identity.MANAGER_VOICE.strip()[:60], system)


class Ordering(Base):
    def test_job_waits_for_the_rollover_hour(self):
        early = datetime(2026, 11, 2, 5, 30, tzinfo=UTC)               # 07:30 Israel
        with redirect_stdout(io.StringIO()):
            self.assertEqual(mb.run_all(early)["skipped"], "before_rollover")
            self.assertEqual(mb.run_all(MONDAY)["sent"], 1)

    def test_deferred_rollover_cuts_at_the_briefing(self):
        saved = mb.briefing_today
        posted = "2026-11-02T06:30:05+00:00"
        mb.briefing_today = lambda cid, day=None: {"channel": "dashboard_chat", "posted_at": posted}
        try:
            later = datetime(2026, 11, 2, 7, 40, tzinfo=UTC)
            # the manager's window is cut AT the briefing: it opens the new window
            self.assertEqual(chat_rollover.briefing_boundary(27, "dashboard_chat", "2026-11-02", later,
                                                             "2026-11-01T06:00:00+00:00"), posted)
            # another agent's window, or a briefing older than the window: unchanged
            self.assertEqual(chat_rollover.briefing_boundary(27, "dashboard_chat:website", "2026-11-02", later),
                             later.isoformat())
            self.assertEqual(chat_rollover.briefing_boundary(27, "dashboard_chat", "2026-11-02", later,
                                                             "2026-11-02T07:00:00+00:00"), later.isoformat())
        finally:
            mb.briefing_today = saved

    def test_briefing_records_where_it_landed(self):
        self.run_at(MONDAY)
        row = self.db.tables["client_activity"][-1]
        self.assertEqual(row["action_type"], mb.BRIEFING_ACTION)
        self.assertEqual(row["details"]["channel"], "dashboard_chat")
        self.assertEqual(row["details"]["posted_at"], "2026-11-02T06:35:01.000000+00:00")
        self.assertEqual(mb.briefing_today(27, "2026-11-02")["posted_at"], "2026-11-02T06:35:01.000000+00:00")


# ─── The login moment never repeats the briefing ─────────────────────────────

def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


class LoginMomentDedupe(unittest.TestCase):
    STUBS = ("supabase", "core.agent_base", "core.claude_json", "core.whatsapp_service", "agents.engagement_agent",
             "agents.client_agent", "agents.onboarding_agent")

    @classmethod
    def setUpClass(cls):
        cls.saved_modules = {n: sys.modules.get(n) for n in cls.STUBS}
        cls.calls = []
        sys.modules.update({
            "supabase": _module("supabase", create_client=lambda *a: None),
            "core.agent_base": _module("core.agent_base", agent_alert=lambda *a, **k: None,
                                       log_step=lambda *a, **k: None, timed_step=lambda a, s, fn: fn()),
            "core.claude_json": _module("core.claude_json", CHAT_TURN_EFFORT="low", CHAT_TURN_MAX_TOKENS=2000,
                                        CHAT_TURN_MODEL="m", cached_system=lambda *a, **k: list(a),
                                        safe_claude_json_call=lambda *a, **k: cls.calls.append(a) or
                                        {"message": "ועוד דבר מאז הבוקר: ליד חדש.", "asked_for_feedback": False}),
            "core.whatsapp_service": _module("core.whatsapp_service", is_configured=lambda: False,
                                             send_whatsapp=lambda *a: False),
            "agents.client_agent": _module("agents.client_agent", get_client=lambda cid: dict(CLIENT),
                                           log_activity=lambda *a, **k: None,
                                           log_communication=lambda *a, **k: None),
            "agents.onboarding_agent": _module("agents.onboarding_agent", LANGUAGE_RULE="Hebrew."),
        })
        sys.modules.pop("agents.engagement_agent", None)
        from agents import engagement_agent
        cls.ea = engagement_agent

    @classmethod
    def tearDownClass(cls):
        for name, module in cls.saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def setUp(self):
        self.calls.clear()
        self.saved = (self.ea._acted_within, self.ea._collect_login_facts, mb.briefing_today)
        self.ea._acted_within = lambda *a: False
        self.seen_since = []

    def tearDown(self):
        self.ea._acted_within, self.ea._collect_login_facts, mb.briefing_today = self.saved

    def facts(self, new_leads=0):
        def collect(client_id, since=None):
            self.seen_since.append(since)
            return {"part_of_day": "morning", "pending_suggestions": [], "homework_in_progress": [],
                    "recently_delivered": [], "new_leads_this_week": new_leads, "new_won_this_week": 0,
                    "lead_channels": [], "ask_for_feedback": True, "site_traffic": None,
                    "briefing_sent_today": bool(since)}
        self.ea._collect_login_facts = collect

    def test_nothing_new_since_the_briefing_means_silence(self):
        mb.briefing_today = lambda cid, day=None: {"posted_at": "2026-11-02T06:30:00+00:00"}
        self.facts(new_leads=0)
        out = self.ea.run_login_moment(27)
        self.assertEqual(out["reason"], "briefing_covered_today")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.seen_since, ["2026-11-02T06:30:00+00:00"])

    def test_only_what_is_new_is_said(self):
        mb.briefing_today = lambda cid, day=None: {"posted_at": "2026-11-02T06:30:00+00:00"}
        self.facts(new_leads=1)
        out = self.ea.run_login_moment(27)
        self.assertTrue(out["sent"])
        self.assertEqual(len(self.calls), 1)
        self.assertIn("briefing_sent_today", self.calls[0][1])

    def test_no_briefing_today_keeps_the_old_behaviour(self):
        mb.briefing_today = lambda cid, day=None: {}
        self.facts(new_leads=0)
        self.ea.run_login_moment(27)
        self.assertEqual(self.seen_since, [None])
        self.assertEqual(len(self.calls), 1)

    def test_daily_sales_email_is_retired(self):
        out = self.ea.run_daily_engagement()
        self.assertTrue(out["retired"])
        self.assertEqual(out["alerts_sent"], 0)


if __name__ == "__main__":
    unittest.main()
