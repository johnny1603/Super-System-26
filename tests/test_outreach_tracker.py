"""The Outreach page (core/outreach_tracker), the ONE follow-up
(outreach_policy.followup_refusal + gmail_outreach.*_followup*) and the
export (core/export_service).

- Status is derived from the records (found / draft / approved / sent / no
  response after 7 days / replied / removed / failed), with its precedence.
- "Asked to be removed" writes the do-not-contact list FIRST (no list entry →
  no mark), cancels open cards, and every later send / follow-up refuses.
- One follow-up per business, ever: 7 days, no reply/removal/bounce, inside
  the daily cap, with the opt-out line.
- The export carries exactly the Hebrew columns — no ids, tokens or bodies —
  and only the signed-in client's rows.

Stdlib only. Run from the repo root:  python tests/test_outreach_tracker.py -v
"""
import io
import os
import sys
import types
import unittest
import zipfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_google_round2 as r2  # noqa: E402
from core import agent_actions as aa  # noqa: E402

NOW = datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc)


def _ago(**kw) -> str:
    return (NOW - timedelta(**kw)).isoformat()


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.op, self.filters, self.payload, self.n = db, table, "select", [], None, None

    def select(self, *_a):
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def delete(self):
        self.op = "delete"
        return self

    def update(self, row):
        self.op, self.payload = "update", row
        return self

    def eq(self, c, v):
        self.filters.append(lambda r: r.get(c) == v)
        return self

    def in_(self, c, values):
        values = list(values)
        self.filters.append(lambda r: r.get(c) in values)
        return self

    def gte(self, c, v):
        self.filters.append(lambda r: str(r.get(c, "")) >= v)
        return self

    def order(self, c, desc=False):
        self.sort = (c, desc)
        return self

    def limit(self, n):
        self.n = n
        return self

    def execute(self):
        if self.table in self.db.broken or (self.op != "select" and self.table in self.db.broken_writes):
            raise RuntimeError("PGRST205 missing table")
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "insert":
            rows.append(dict(self.payload))
            return types.SimpleNamespace(data=[self.payload])
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "delete":
            self.db.tables[self.table] = [r for r in rows if r not in hit]
            return types.SimpleNamespace(data=hit)
        if getattr(self, "sort", None):
            hit.sort(key=lambda r: str(r.get(self.sort[0]) or ""), reverse=self.sort[1])
        return types.SimpleNamespace(data=hit[:self.n or None])


class _DB:
    def __init__(self):
        self.tables, self.broken, self.broken_writes = {}, set(), set()

    def table(self, name):
        return _Q(self, name)


def _send(email, sent_at, kind="first", client_id=27, **extra):
    return {"client_id": client_id, "to_email": email, "business_name": extra.get("business_name", "Lia Bridal"),
            "subject": extra.get("subject", "Shoes for your brides"), "body": extra.get("body", "first body"),
            "source_url": extra.get("source_url", "https://liabridal.co.il/"),
            "from_email": "owner@shoes.co.il", "gmail_message_id": extra.get("mid", "gm-" + email),
            "sent_at": sent_at, "kind": kind}


def _action(email, status, created_at, action="send_outreach_email", client_id=27, aid=1, **extra):
    return {"id": aid, "client_id": client_id, "action": action, "status": status, "created_at": created_at,
            "outcome": extra.get("outcome"),
            "params": {"to_email": email, "business_name": extra.get("business_name", "Biz"),
                       "subject": "Hello", "source_url": "https://biz.co.il/"}}


class Base(r2.Services):
    def setUp(self):
        super().setUp()
        self.flags_on()
        from core import export_service, gmail_agent_plan, gmail_outreach, outreach_policy, outreach_tracker
        self.go, self.policy, self.plan, self.ot, self.ex = (gmail_outreach, outreach_policy, gmail_agent_plan,
                                                             outreach_tracker, export_service)
        self.db = _DB()
        self.db.tables["client_accounts"] = [{"client_id": 27, "platform": "gmail_send", "status": "active",
                                              "account_id": "owner@shoes.co.il", "access_token": "rt-secret",
                                              "id": 1}]
        self.sent_messages, self.claimed = [], []
        self.saved = {
            (gmail_outreach, "_db"): gmail_outreach._db, (gmail_outreach, "_client"): gmail_outreach._client,
            (outreach_policy, "_db"): outreach_policy._db, (outreach_tracker, "_db"): outreach_tracker._db,
            (outreach_tracker, "_now"): outreach_tracker._now,
            (gmail_agent_plan, "entitled"): gmail_agent_plan.entitled,
            (gmail_agent_plan, "require_budget"): gmail_agent_plan.require_budget,
            (gmail_agent_plan, "_count"): gmail_agent_plan._count,
            (self.gmail, "send_message"): self.gmail.send_message,
            (aa, "_claim"): aa._claim,
        }
        gmail_outreach._db = outreach_policy._db = outreach_tracker._db = lambda: self.db
        gmail_outreach._client = lambda cid: {"id": cid, "name": "Shoes by Dana"}
        outreach_tracker._now = lambda: NOW
        gmail_agent_plan.entitled = lambda cid: True
        gmail_agent_plan.require_budget = lambda cid, est: None
        gmail_agent_plan._count = lambda counter: 1
        self.gmail.send_message = lambda rt, message: self.sent_messages.append(message) or {"id": "gm-new"}
        aa._claim = lambda aid, frm, changes: self.claimed.append((aid, changes)) or True

    def tearDown(self):
        for (module, name), fn in self.saved.items():
            setattr(module, name, fn)
        super().tearDown()

    def rows(self, client_id=27):
        return {r["key"]: r for r in self.ot.collect(client_id)["rows"]}


# ─── Status ──────────────────────────────────────────────────────────────────

class Status(Base):
    def setUp(self):
        super().setUp()
        t = self.db.tables
        t["prospect_contacts"] = [{"client_id": 27, "email": "found@a.co.il", "business_name": "A Studio",
                                   "area": "תל אביב", "source": "places", "source_url": "https://a.co.il/",
                                   "found_at": _ago(days=1), "place_id": "pl-a"}]
        t["agent_actions"] = [
            _action("draft@b.co.il", "proposed", _ago(hours=1), aid=1),
            _action("old@c.co.il", "proposed", _ago(hours=30), aid=2),        # expired, nothing else
            _action("busy@d.co.il", "running", _ago(minutes=1), aid=3),
        ]
        t["outreach_sends"] = [
            _send("fresh@e.co.il", _ago(days=3)),
            _send("quiet@f.co.il", _ago(days=8)),
            _send("answered@g.co.il", _ago(days=8)),
            _send("bounce@h.co.il", _ago(days=2)),
            _send("blocked@i.co.il", _ago(days=9)),
            _send("followed@j.co.il", _ago(days=12)), _send("followed@j.co.il", _ago(days=2), kind="followup"),
        ]
        t["outreach_marks"] = [
            {"client_id": 27, "to_email": "answered@g.co.il", "mark": "replied", "marked_at": _ago(days=1)},
            {"client_id": 27, "to_email": "bounce@h.co.il", "mark": "bounced", "marked_at": _ago(days=1)},
            {"client_id": 27, "to_email": "blocked@i.co.il", "mark": "replied", "marked_at": _ago(days=1)},
        ]
        t["outreach_suppression"] = [{"key_type": "domain", "key_value": "i.co.il"}]

    def test_every_status_is_derived_from_the_records(self):
        rows = self.rows()
        self.assertEqual({k: r["status"] for k, r in rows.items()}, {
            "found@a.co.il": "found", "draft@b.co.il": "draft_pending", "busy@d.co.il": "approved",
            "fresh@e.co.il": "sent", "quiet@f.co.il": "no_response", "answered@g.co.il": "replied",
            "bounce@h.co.il": "failed", "blocked@i.co.il": "removed", "followed@j.co.il": "sent"})
        self.assertNotIn("old@c.co.il", rows)          # an expired draft alone is not a business

    def test_follow_up_is_due_only_after_seven_quiet_days_and_only_once(self):
        rows = self.rows()
        self.assertEqual([k for k, r in rows.items() if r["followup_due"]], ["quiet@f.co.il"])
        self.assertEqual(rows["followed@j.co.il"]["followup_label"], "נשלח מעקב")
        self.assertEqual(self.ot.NO_RESPONSE_DAYS, 7)

    def test_removed_wins_over_everything(self):
        self.assertEqual(self.rows()["blocked@i.co.il"]["status_label"], "ביקשו להסיר")

    def test_unreadable_suppression_list_shows_everyone_as_removed(self):
        self.db.broken.add("outreach_suppression")
        data = self.ot.collect(27)
        self.assertEqual({r["status"] for r in data["rows"]}, {"removed"})
        self.assertIn("outreach_suppression", data["gaps"])

    def test_counters_and_page(self):
        page = self.ot.page(27)
        c = page["counters"]
        self.assertEqual((c["found"], c["sent"], c["replied"], c["removed"]), (9, 6, 1, 1))
        self.assertEqual(page["cap"]["limit"], 10)
        self.assertTrue(page["sending_enabled"])

    def test_agent_snapshot_never_gets_fake_zeros(self):
        self.assertEqual([f["to_email"] for f in self.ot.for_agent(27)["followup_due"]], ["quiet@f.co.il"])
        self.db.broken.add("outreach_marks")
        with self.assertRaises(RuntimeError):
            self.ot.for_agent(27)

    def test_rows_carry_israel_dates_and_a_gmail_link(self):
        row = self.rows()["fresh@e.co.il"]
        self.assertRegex(row["sent_date"], r"^\d{2}/\d{2}/\d{4}$")
        self.assertTrue(row["gmail_link"].startswith("https://mail.google.com/mail/u/"))
        self.assertIn("gm-fresh", row["gmail_link"])


class IsraelDates(unittest.TestCase):
    def test_day_month_year_in_israel_time(self):
        from core.outreach_tracker import israel_date
        self.assertEqual(israel_date("2026-10-29T22:30:00+00:00"), "30/10/2026")   # winter, UTC+2
        self.assertEqual(israel_date("2026-07-01T21:30:00Z"), "02/07/2026")        # summer, UTC+3
        self.assertEqual(israel_date(""), "")


# ─── The client's marks ──────────────────────────────────────────────────────

class Marks(Base):
    def setUp(self):
        super().setUp()
        self.db.tables["outreach_sends"] = [_send("hello@liabridal.co.il", _ago(days=8)),
                                            _send("dana.biz@gmail.com", _ago(days=8),
                                                  source_url="https://danabiz.co.il/")]
        self.db.tables["prospect_contacts"] = [{"client_id": 27, "email": "new@k.co.il", "source": "chat",
                                                "source_url": "https://k.co.il/", "found_at": _ago(days=1),
                                                "place_id": "pl-k"}]
        self.db.tables["agent_actions"] = [_action("hello@liabridal.co.il", "proposed", _ago(hours=1),
                                                   action="send_outreach_followup", aid=7)]

    def test_removed_writes_the_do_not_contact_list_first_and_blocks_every_later_send(self):
        result = self.ot.mark(27, ["hello@liabridal.co.il"], "removed")
        self.assertEqual(result["done"], ["hello@liabridal.co.il"])
        keys = {(r["key_type"], r["key_value"]) for r in self.db.tables["outreach_suppression"]}
        self.assertEqual(keys, {("email", "hello@liabridal.co.il"), ("domain", "liabridal.co.il")})
        self.assertEqual(self.db.tables["outreach_marks"][0]["mark"], "removed")
        self.assertEqual([aid for aid, _ in self.claimed], [7])          # the open follow-up card
        self.assertEqual(self.claimed[0][1]["status"], "cancelled")
        self.assertEqual(self.rows()["hello@liabridal.co.il"]["status"], "removed")
        # Every later path refuses
        self.assertEqual(self.policy.followup_refusal(27, "hello@liabridal.co.il", now=NOW), "suppressed")
        with self.assertRaises(self.go.SendFailed) as ctx:
            self.go.send_approved(27, {"to_email": "hello@liabridal.co.il", "body": self.policy.with_opt_out("x", "he"),
                                       "language": "he"})
        self.assertEqual(ctx.exception.code, "suppressed")
        self.assertEqual(self.sent_messages, [])

    def test_free_mail_address_never_suppresses_the_whole_provider(self):
        self.ot.mark(27, ["dana.biz@gmail.com"], "removed")
        keys = {(r["key_type"], r["key_value"]) for r in self.db.tables["outreach_suppression"]}
        self.assertEqual(keys, {("email", "dana.biz@gmail.com")})

    def test_place_id_is_suppressed_too(self):
        self.ot.mark(27, ["new@k.co.il"], "removed")
        keys = {(r["key_type"], r["key_value"]) for r in self.db.tables["outreach_suppression"]}
        self.assertIn(("place_id", "pl-k"), keys)

    def test_no_list_entry_means_no_mark(self):
        self.db.broken_writes.add("outreach_suppression")
        result = self.ot.mark(27, ["hello@liabridal.co.il"], "removed")
        self.assertEqual(result["failed"], [{"key": "hello@liabridal.co.il", "code": "not_saved"}])
        self.assertEqual(self.db.tables.get("outreach_marks", []), [])
        self.assertEqual(self.claimed, [])

    def test_marking_twice_is_harmless(self):
        self.ot.mark(27, ["hello@liabridal.co.il"], "removed")
        again = self.ot.mark(27, ["hello@liabridal.co.il"], "removed")
        self.assertEqual(again["failed"][0]["code"], "not_markable")
        self.assertEqual(len(self.db.tables["outreach_suppression"]), 2)

    def test_replied_only_after_a_send_and_it_stops_the_follow_up(self):
        self.assertEqual(self.ot.mark(27, ["new@k.co.il"], "replied")["failed"][0]["code"], "not_markable")
        self.ot.mark(27, ["hello@liabridal.co.il"], "replied")
        self.assertEqual(self.policy.followup_refusal(27, "hello@liabridal.co.il", now=NOW), "marked_replied")
        self.assertFalse(self.rows()["hello@liabridal.co.il"]["followup_due"])

    def test_unknown_mark_and_missing_table(self):
        with self.assertRaises(self.ot.OutreachError):
            self.ot.mark(27, ["hello@liabridal.co.il"], "deleted")
        self.db.broken.add("outreach_marks")
        with self.assertRaises(self.ot.OutreachError) as ctx:
            self.ot.mark(27, ["hello@liabridal.co.il"], "removed")
        self.assertEqual(ctx.exception.code, "not_ready")

    def test_delete_only_found_or_draft_rows(self):
        result = self.ot.delete(27, ["new@k.co.il", "hello@liabridal.co.il"])
        self.assertEqual(result["done"], ["new@k.co.il"])
        self.assertEqual(result["failed"], [{"key": "hello@liabridal.co.il", "code": "has_send_record"}])
        self.assertEqual(self.db.tables["prospect_contacts"], [])
        self.assertEqual(len(self.db.tables["outreach_sends"]), 2)        # the evidence stays

    def test_another_clients_business_cannot_be_touched(self):
        self.db.tables["outreach_sends"].append(_send("other@z.co.il", _ago(days=8), client_id=31))
        result = self.ot.mark(27, ["other@z.co.il"], "removed")
        self.assertEqual(result["failed"], [{"key": "other@z.co.il", "code": "not_found"}])
        self.assertEqual(self.ot.delete(27, ["other@z.co.il"])["failed"][0]["code"], "not_found")
        self.assertNotIn("outreach_suppression", {k for k, v in self.db.tables.items() if v})


# ─── The ONE follow-up ───────────────────────────────────────────────────────

class FollowUp(Base):
    EMAIL = "hello@liabridal.co.il"

    def setUp(self):
        super().setUp()
        self.db.tables["outreach_sends"] = [_send(self.EMAIL, _ago(days=8), business_name="Lia Bridal Studio",
                                                  body="Hello Lia Bridal Studio, first message")]

    def params(self, **changes):
        return {**self.go.prepare_followup(27, {"to_email": self.EMAIL,
                                                "body": "Hi again Lia Bridal Studio — did you see my note?"},
                                           "en", []), **changes}

    def test_allowed_after_seven_days_with_name_from_our_record(self):
        self.assertEqual(self.policy.followup_refusal(27, self.EMAIL), "")
        params = self.go.prepare_followup(27, {"to_email": self.EMAIL, "business_name": "Model Invented Name",
                                               "body": "Hi again Lia Bridal Studio"}, "en", [])
        self.assertEqual(params["business_name"], "Lia Bridal Studio")
        self.assertTrue(params["body"].endswith(self.policy.opt_out_line("en")))
        self.assertEqual(set(params), self.go.FOLLOWUP_KEYS)

    def test_too_soon_and_no_first_message(self):
        self.db.tables["outreach_sends"][0]["sent_at"] = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        self.assertEqual(self.policy.followup_refusal(27, self.EMAIL), "too_soon")
        self.assertEqual(self.policy.followup_refusal(27, "nobody@x.co.il"), "no_first_message")

    def test_marks_and_suppression_refuse(self):
        for mark, code in (("replied", "marked_replied"), ("bounced", "marked_bounced"), ("removed", "suppressed")):
            with self.subTest(mark=mark):
                self.db.tables["outreach_marks"] = [{"client_id": 27, "to_email": self.EMAIL, "mark": mark,
                                                     "marked_at": _ago(hours=1)}]
                self.assertEqual(self.policy.followup_refusal(27, self.EMAIL), code)
        self.db.tables["outreach_marks"] = []
        self.db.tables["outreach_suppression"] = [{"key_type": "email", "key_value": self.EMAIL}]
        self.assertEqual(self.policy.followup_refusal(27, self.EMAIL), "suppressed")

    def test_fails_closed_when_unreadable(self):
        self.db.broken.add("outreach_marks")
        self.assertEqual(self.policy.followup_refusal(27, self.EMAIL), "record_unavailable")
        self.db.broken.add("outreach_suppression")
        self.assertEqual(self.policy.followup_refusal(27, self.EMAIL), "suppressed")

    def test_one_follow_up_ever(self):
        params = self.params()
        self.go.send_followup_approved(27, params)
        (record,) = [s for s in self.db.tables["outreach_sends"] if s["kind"] == "followup"]
        self.assertEqual(record["to_email"], self.EMAIL)
        self.assertIn(self.policy.opt_out_line("en"), self.sent_messages[0].get_content())
        with self.assertRaises(self.go.SendFailed) as ctx:
            self.go.send_followup_approved(27, params)
        self.assertEqual(ctx.exception.code, "followup_not_allowed")
        with self.assertRaises(self.go.DraftRefused) as ctx:
            self.go.prepare_followup(27, {"to_email": self.EMAIL, "body": "Lia Bridal Studio again"}, "en", [])
        self.assertEqual(ctx.exception.code, "followup_already_sent")
        self.assertEqual(len(self.sent_messages), 1)

    def test_inside_the_daily_cap(self):
        params = self.params()
        today = datetime.now(timezone.utc).isoformat()
        self.db.tables["outreach_sends"] += [_send(f"x{i}@a.co", today) for i in range(10)]
        with self.assertRaises(self.go.SendFailed) as ctx:
            self.go.send_followup_approved(27, params)
        self.assertEqual(ctx.exception.code, "daily_cap")
        self.assertEqual(self.sent_messages, [])

    def test_needs_the_opt_out_line_and_the_flag(self):
        params = self.params()
        with self.assertRaises(self.go.SendFailed) as ctx:
            self.go.send_followup_approved(27, {**params, "body": "Lia Bridal Studio, no opt out"})
        self.assertEqual(ctx.exception.code, "no_opt_out")
        os.environ.pop("GMAIL_SEND_ENABLED")
        with self.assertRaises(self.go.SendFailed) as ctx:
            self.go.send_followup_approved(27, params)
        self.assertEqual(ctx.exception.code, "send_disabled")
        self.assertEqual(self.sent_messages, [])

    def test_never_to_a_removed_business_even_with_an_old_card(self):
        params = self.params()
        self.db.tables["outreach_suppression"] = [{"key_type": "domain", "key_value": "liabridal.co.il"}]
        with self.assertRaises(self.go.SendFailed) as ctx:
            self.go.send_followup_approved(27, params)
        self.assertEqual(ctx.exception.code, "suppressed")
        self.assertEqual(self.sent_messages, [])

    def test_repeat_of_the_first_message_and_a_pending_card_refused(self):
        with self.assertRaises(self.go.DraftRefused) as ctx:
            self.go.prepare_followup(27, {"to_email": self.EMAIL, "body": "Hello Lia Bridal Studio, first message"},
                                     "en", [])
        self.assertEqual(ctx.exception.code, "not_personal")
        with self.assertRaises(self.go.DraftRefused) as ctx:
            self.go.prepare_followup(27, {"to_email": self.EMAIL, "body": "x"}, "en",
                                     [{"params": {"to_email": self.EMAIL}}])
        self.assertEqual(ctx.exception.code, "followup_pending")

    def test_card_is_client_approved_and_offered_only_when_due(self):
        spec = aa.ACTIONS["send_outreach_followup"]
        self.assertEqual(spec["approver"], "client")
        self.assertFalse(spec["available"]({"outreach": {"followup_due": []}}, {"id": 27}))
        self.assertTrue(spec["available"]({"outreach": {"followup_due": [{"to_email": self.EMAIL}]}}, {"id": 27}))
        for table in (aa._FOLLOWUP_DRAFT_REFUSALS,):
            for code, texts in table.items():
                self.assertEqual(set(texts), set(aa.LANGS), code)


# ─── Export ──────────────────────────────────────────────────────────────────

class Export(Base):
    def setUp(self):
        super().setUp()
        self.db.tables["outreach_sends"] = [
            _send("hello@liabridal.co.il", _ago(days=2), subject="=HYPERLINK(\"http://evil\")"),
            _send("mine@b.co.il", _ago(days=1)),
            _send("hello@liabridal.co.il", _ago(days=1), client_id=31, business_name="Other client's"),
        ]

    def test_exact_hebrew_columns_and_nothing_else(self):
        self.assertEqual([h for _k, h in self.ot.COLUMNS], [
            "שם העסק", "עיר / אזור", "מקור", "המייל שאליו פנינו", "העמוד באתר העסק", "נושא",
            "תאריך שליחה", "סטטוס", "מעקב", "קישור ב-Gmail"])
        self.assertEqual(self.ot.ADMIN_COLUMNS[0], ("client_label", "לקוח"))
        columns, table = self.ot.export_table(self.ot.page(27)["rows"])
        for line in table:
            self.assertEqual(set(line), {k for k, _h in columns})
            joined = " ".join(str(v) for v in line.values())
            self.assertNotIn("rt-secret", joined)
            self.assertNotIn("first body", joined)          # the e-mail body is not exported

    def test_only_the_signed_in_clients_rows(self):
        rows = self.ot.page(27)["rows"]
        self.assertEqual({r["client_id"] for r in rows}, {27})
        # A selection naming another client's row matches nothing
        self.assertEqual(self.ot.filter_rows(rows, keys=["31|hello@liabridal.co.il"], client_id=27), [])
        mine = self.ot.filter_rows(rows, keys=["hello@liabridal.co.il"], client_id=27)
        self.assertEqual([(r["client_id"], r["business_name"]) for r in mine], [(27, "Lia Bridal")])

    def test_the_endpoint_builds_rows_for_the_session_client_only(self):
        source = r2._function_source("core/api_server.py", "client_outreach_export")
        self.assertIn("client_id = _require_session(request)", source)
        self.assertIn("outreach_tracker.page(client_id)", source)
        self.assertNotIn("req.client_id", source)
        self.assertIn("client_id=client_id", source)

    def test_admin_columns_and_filter(self):
        data = self.ot.collect(None)
        columns, table = self.ot.export_table(self.ot.filter_rows(data["rows"], client_id=31), admin=True,
                                              client_names={31: "Dana"})
        self.assertEqual(columns, self.ot.ADMIN_COLUMNS)
        self.assertEqual([l["client_label"] for l in table], ["Dana (#31)"])

    def test_csv_with_bom_for_excel_without_for_sheets_and_no_formulas(self):
        columns, table = self.ot.export_table(self.ot.page(27)["rows"])
        excel = self.ex.to_csv(columns, table, bom=True)
        sheets = self.ex.to_csv(columns, table, bom=False)
        self.assertTrue(excel.startswith(b"\xef\xbb\xbf"))
        self.assertFalse(sheets.startswith(b"\xef\xbb\xbf"))
        text = sheets.decode("utf-8")
        self.assertTrue(text.startswith("שם העסק,"))
        self.assertIn("'=HYPERLINK", text)
        self.assertNotIn(",=HYPERLINK", text)

    def test_word_and_excel_files_are_valid(self):
        columns, table = self.ot.export_table(self.ot.page(27)["rows"])
        summary = self.ot.summary_lines(self.ot.page(27)["rows"], {"sent_today": 0, "limit": 10})
        docx = zipfile.ZipFile(io.BytesIO(self.ex.to_docx("פניות לעסקים", "Shoes", columns, table, summary)))
        document = docx.read("word/document.xml").decode("utf-8")
        for needle in ("שם העסק", "נשלחו: 2", "<w:bidi/>", "<w:bidiVisual/>", "Lia Bridal"):
            self.assertIn(needle, document)
        self.assertIn("[Content_Types].xml", docx.namelist())
        xlsx = zipfile.ZipFile(io.BytesIO(self.ex.to_xlsx("פניות לעסקים", columns, table)))
        self.assertIn("xl/worksheets/sheet1.xml", xlsx.namelist())
        html = self.ex.print_html("פניות לעסקים", "Shoes", columns, table, summary=summary)
        self.assertIn("נשלחו: 2", html)

    def test_no_google_api_or_scope_in_the_export_path(self):
        source = r2._function_source("core/api_server.py", "_outreach_file")
        for banned in ("drive_service", "googleapis", "scope", "oauth"):
            self.assertNotIn(banned, source.split('"""', 2)[-1].lower())


# ─── Wiring ──────────────────────────────────────────────────────────────────

class Wiring(unittest.TestCase):
    def test_admin_view_reads_the_page_but_cannot_mark_delete_or_export(self):
        from core import admin_view
        self.assertTrue(admin_view.allowed("client_outreach", "GET"))
        for name in ("client_outreach_mark", "client_outreach_delete", "client_outreach_export"):
            self.assertFalse(admin_view.allowed(name, "POST"), name)

    def test_dashboard_page_is_listed_and_the_removal_button_is_explained(self):
        html = r2._read("dashboard", "client", "index.html")
        self.assertIn("'outreach'", html.split("const VIEWS = [", 1)[1].split("]", 1)[0])
        self.assertIn('id="viewOutreach"', html)
        self.assertIn("ביקשו להסיר", html)
        self.assertIn("לא תשלח לעסק הזה שום מייל נוסף", html)
        admin = r2._read("dashboard", "admin", "index.html")
        self.assertIn('id="tab-outreach"', admin)
        self.assertIn("/api/admin/outreach/export", admin)

    def test_migration_has_the_marks_table_and_the_send_kind(self):
        sql = r2._read("migrations", "2026-10-30-gmail-outreach.sql")
        self.assertIn("outreach_marks", sql)
        self.assertIn("'replied'", sql)
        self.assertIn("'removed'", sql)
        self.assertIn("'followup'", sql)


if __name__ == "__main__":
    unittest.main()
