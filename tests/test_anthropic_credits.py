"""Claude credits widget (core/anthropic_credits, docs/ANTHROPIC-CREDITS.md):
measured Claude spend from client_costs only, the owner's balance entries,
remaining / burn / days left, green-yellow-red with editable thresholds, ONE
notice per crossing (warn only), audit rows — and the admin-only surface.
Stdlib unittest with an in-memory Supabase fake."""
import os
import re
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from core import anthropic_credits as ac   # noqa: E402

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Query:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self.gte_ = db, name, {}, None
        self.desc, self.order_key, self.lim, self.rng, self.op, self.payload = False, None, None, None, "select", None

    def select(self, *a): return self
    def eq(self, k, v): self.filters[k] = v; return self
    def gte(self, k, v): self.gte_ = (k, v); return self
    def order(self, k, desc=False): self.order_key, self.desc = k, desc; return self
    def limit(self, n): self.lim = n; return self
    def range(self, a, b): self.rng = (a, b); return self
    def insert(self, row): self.op, self.payload = "insert", row; return self
    def upsert(self, row, on_conflict=None): self.op, self.payload = "upsert", row; return self

    def execute(self):
        if self.name in self.db.missing:
            raise RuntimeError("relation does not exist")
        rows = self.db.tables.setdefault(self.name, [])
        if self.op == "insert":
            row = dict(self.payload, id=len(rows) + 1)
            row.setdefault("entered_at", self.db.clock().isoformat())
            rows.append(row)
            return types.SimpleNamespace(data=[row])
        if self.op == "upsert":
            rows[:] = [r for r in rows if r.get("key") != self.payload["key"]] + [dict(self.payload)]
            return types.SimpleNamespace(data=[self.payload])
        out = [r for r in rows if all(r.get(k) == v for k, v in self.filters.items())]
        if self.gte_:
            out = [r for r in out if str(r.get(self.gte_[0])) >= self.gte_[1]]
        if self.order_key:
            out.sort(key=lambda r: str(r.get(self.order_key)), reverse=self.desc)
        if self.rng:
            out = out[self.rng[0]:self.rng[1] + 1]
        if self.lim:
            out = out[:self.lim]
        return types.SimpleNamespace(data=out)


class FakeDB:
    def __init__(self):
        self.tables, self.missing, self.now = {}, set(), NOW

    def clock(self):
        return self.now

    def table(self, name):
        return Query(self, name)


def cost(hours_ago, usd, claude=True, at=NOW):
    details = {"usd": usd, "input_tokens": 100, "output_tokens": 50, "model": "claude-opus-5-5"} if claude \
        else {"usd": usd, "estimate": True, "model": "some-video-model"}
    return {"created_at": (at - timedelta(hours=hours_ago)).isoformat(), "amount": usd * 3.4, "details": details}


class CreditsCase(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.saved_db = ac._db
        ac._db = lambda: self.db
        self.notices = []
        self.saved_mods = {n: sys.modules.get(n) for n in ("agents.master_agent", "core.email_service")}
        sys.modules["agents.master_agent"] = types.SimpleNamespace(
            alert=lambda label, issues: self.notices.append(("alert", label, issues)))
        sys.modules["core.email_service"] = types.SimpleNamespace(
            send_owner_notice=lambda subject, text: self.notices.append(("email", subject, text)) or True)

    def tearDown(self):
        ac._db = self.saved_db
        for name, mod in self.saved_mods.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod

    def balance(self, usd, hours_ago):
        self.db.now = NOW - timedelta(hours=hours_ago)
        row = ac.record_balance(usd, "top-up", "admin session x from 1.2.3.4")
        self.db.now = NOW
        return row


class Measured(CreditsCase):
    def test_only_anthropic_rows_count(self):
        self.assertTrue(ac.is_claude_row({"details": {"input_tokens": 1}}))
        self.assertTrue(ac.is_claude_row({"details": {"web_searches": 2}}))
        self.assertFalse(ac.is_claude_row({"details": {"estimate": True, "model": "kling"}}))   # client-paid media
        self.assertFalse(ac.is_claude_row({"details": {"sku": "places"}}))
        self.assertFalse(ac.is_claude_row({"details": {}}))

    def test_spend_windows_burn_and_days_left(self):
        self.db.tables["client_costs"] = [cost(1, 2.0), cost(30, 3.0), cost(100, 9.0), cost(2, 50.0, claude=False),
                                          cost(24 * 10, 100.0)]
        self.balance(100.0, 48)                     # entered 2 days ago
        view = ac.estimate(NOW)
        self.assertEqual(view["spend_24h_usd"], 2.0)
        self.assertEqual(view["spend_7d_usd"], 14.0)                  # 2+3+9 (not the media row, not 10 days ago)
        self.assertEqual(view["spent_since_balance_usd"], 5.0)        # 2+3 since the entry
        self.assertEqual(view["remaining_usd"], 95.0)
        self.assertEqual(view["daily_burn_usd"], 2.0)                 # 14 / 7
        self.assertEqual(view["days_left"], 47.5)
        self.assertEqual(view["status"], "green")
        self.assertEqual(view["console_url"], "https://console.anthropic.com/settings/billing/")
        self.assertIn("מקור האמת", view["note"])

    def test_pages_through_many_rows(self):
        self.db.tables["client_costs"] = [cost(1 + i / 1000, 0.01) for i in range(2500)]
        rows, truncated = ac.claude_costs_since(NOW - timedelta(days=7))
        self.assertEqual((len(rows), truncated), (2500, False))

    def test_no_balance_is_unknown_and_missing_table_is_said(self):
        self.db.tables["client_costs"] = [cost(1, 1.0)]
        view = ac.estimate(NOW)
        self.assertEqual((view["status"], view["remaining_usd"], view["balance"]), ("unknown", None, None))
        self.assertEqual(view["spend_24h_usd"], 1.0)                  # measured spend still shows
        self.db.missing.add(ac.TABLE)
        view = ac.estimate(NOW)
        self.assertTrue(view["migration_pending"])
        with self.assertRaises(ac.MigrationPending):
            ac.record_balance(10)


class Status(CreditsCase):
    def test_default_thresholds(self):
        cfg = ac.DEFAULT_SETTINGS
        self.assertEqual(cfg, {"yellow_days": 7.0, "red_days": 2.0})
        self.assertEqual(ac.status_for(100, 10, cfg), "green")
        self.assertEqual(ac.status_for(100, 6.9, cfg), "yellow")
        self.assertEqual(ac.status_for(100, 1.9, cfg), "red")
        self.assertEqual(ac.status_for(0, None, cfg), "red")
        self.assertEqual(ac.status_for(-3, 0, cfg), "red")
        self.assertEqual(ac.status_for(100, None, cfg), "green")        # no burn this week
        self.assertEqual(ac.status_for(None, None, cfg), "unknown")

    def test_thresholds_are_configurable_validated_and_audited(self):
        ac.set_thresholds(10, 3, "admin")
        self.assertEqual(ac.settings(), {"yellow_days": 10.0, "red_days": 3.0})
        for bad in ((2, 3), (0, 0), (100, 2), ("x", 1)):
            with self.assertRaises(ValueError):
                ac.set_thresholds(*bad)
        audit = [r for r in self.db.tables["alerts"] if r["source"] == "credits"]
        self.assertEqual(len(audit), 1)
        self.assertIn("yellow<10.0d red<3.0d", audit[0]["issues"][0])

    def test_balance_is_validated_and_audited_with_the_previous_value(self):
        for bad in (-1, "abc", 2_000_000):
            with self.assertRaises(ValueError):
                ac.record_balance(bad)
        self.balance(100, 5)
        self.balance(250.5, 1)
        lines = [r["issues"][0] for r in self.db.tables["alerts"] if r["source"] == "credits"]
        self.assertEqual(len(lines), 2)
        self.assertIn("$250.50", lines[1])
        self.assertIn("was $100.00", lines[1])
        self.assertEqual(ac.latest_balance()["balance_usd"], 250.5)
        self.assertEqual(self.db.tables["alerts"][0]["status"], "resolved")
        from core import bulk_delete
        self.assertIn("credits", bulk_delete.AUDIT_SOURCES)          # protected like the other audit rows


class OneNoticePerCrossing(CreditsCase):
    def notices_of(self, kind):
        return [n for n in self.notices if n[0] == kind]

    def test_each_crossing_notifies_once_and_a_top_up_rearms(self):
        self.balance(30.0, 1)
        self.db.tables["client_costs"] = [cost(24 * d + 2, 3.5) for d in range(7)]   # burn 3.5/day
        view = ac.check(NOW)                         # 30 - 0 = 30 → ~8.6 days: green
        self.assertEqual((view["status"], view["notified"]), ("green", False))
        self.db.tables["client_costs"] += [cost(0.5, 10.0)]                      # remaining 20 → yellow
        view = ac.check(NOW)
        self.assertEqual((view["status"], view["notified"]), ("yellow", True))
        self.assertEqual(ac.check(NOW)["notified"], False)                     # same level: no second notice
        self.db.tables["client_costs"] += [cost(0.4, 14.0)]                      # remaining 6 → red
        self.assertEqual((ac.check(NOW)["status"], ac.check(NOW)["notified"]), ("red", False))
        self.assertEqual(len(self.notices_of("email")), 2)                      # yellow once, red once
        self.assertEqual(len(self.notices_of("alert")), 2)
        self.assertIn("console.anthropic.com/settings/billing", self.notices_of("email")[-1][2])
        self.assertIn("שום סוכן לא נעצר", self.notices_of("email")[-1][2])
        self.balance(500.0, 0.1)                                               # top-up: green, re-armed
        self.assertEqual(ac.check(NOW)["status"], "green")
        self.db.tables["client_costs"] += [cost(0.05, 480.0)]                    # crosses again → notified again
        self.assertTrue(ac.check(NOW)["notified"])
        self.assertEqual(len(self.notices_of("email")), 3)

    def test_unknown_never_notifies(self):
        self.db.tables["client_costs"] = [cost(1, 100.0)]
        self.assertEqual(ac.check(NOW)["notified"], False)
        self.assertFalse(self.notices)

    def test_warn_only_nothing_is_stopped(self):
        src = _read("core", "anthropic_credits.py")
        for forbidden in ("operator_flags", "set_flag", "MODEL_ROUTING_ENABLED", "pause", "disable"):
            self.assertNotIn(forbidden, src.split('"""', 2)[2])   # code, not the docstring
        self.assertNotRegex(src, r"import anthropic|api\.anthropic\.com|ANTHROPIC_ADMIN")   # no new call, no key


class Surface(unittest.TestCase):
    def test_routes_are_admin_session_or_admin_key_only(self):
        api = _read("core", "api_server.py")
        for route in ('@app.get("/api/admin/credits")', '@app.post("/api/admin/credits/balance")',
                      '@app.put("/api/admin/credits/thresholds")'):
            body = api[api.index(route):api.index(route) + 400]
            self.assertIn("_require_admin(request)", body, route)
        self.assertIn('@app.get("/api/credits/check", dependencies=_admin_only)', api)

    def test_widget_only_on_admin_pages(self):
        for page in ("index.html", "operator/index.html", "models/index.html", "landing/index.html",
                     "connections/index.html"):
            self.assertIn('/assets/credits-widget.js', _read("dashboard", "admin", *page.split("/")), page)
        for root, _, files in os.walk(os.path.join(ROOT, "dashboard")):
            if os.sep + "admin" in root:
                continue
            for name in files:
                if name.endswith(".html"):
                    with open(os.path.join(root, name), encoding="utf-8") as f:
                        self.assertNotIn("credits-widget.js", f.read(), os.path.join(root, name))

    def test_widget_top_up_link_and_note(self):
        js = _read("dashboard", "assets", "credits-widget.js")
        self.assertIn("https://console.anthropic.com/settings/billing/", js)
        self.assertIn("top.target = '_blank'", js)
        self.assertIn("top.rel = 'noopener noreferrer'", js)
        self.assertIn("הקונסולה של Anthropic היא מקור האמת", js)
        self.assertIn("Auto reload", js)
        self.assertIn("if (e.auth) return;", js)       # no admin session → nothing drawn
        self.assertNotRegex(js, r"\.appendChild\((root|panel)\)[^\n]*\n[^\n]*mousedown")

    def test_migration_idempotent_and_job_scheduled(self):
        sql = _read("migrations", "2026-11-09-anthropic-credits.sql")
        self.assertIn("create table if not exists anthropic_credit_balances", sql)
        self.assertIn("create index if not exists", sql)
        jobs = _read("scripts", "schedule_jobs.sh")
        self.assertRegex(jobs, r'job credits-check GET "[^"]+" "/api/credits/check" active')
        self.assertIn("anthropic_credits:", _read("core", "admin_service.py"))   # not a global setting row


if __name__ == "__main__":
    unittest.main()
