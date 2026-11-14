"""Alert hygiene (core/alert_hygiene, docs/SYSTEM-REVIEW.md Part 0): one open
alert per key, alerts that close when the connection works, the reconnect
write landing in the row the readers read, and 401 = reconnect.

Stdlib only. Run from the repo root:  python tests/test_alert_hygiene.py -v
"""
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import alert_hygiene as ah   # noqa: E402


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class _Q:
    def __init__(self, db):
        self.db, self.op, self.filters, self.payload, self.ids = db, "select", {}, None, None

    def select(self, *_a):
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def in_(self, col, values):
        if col == "id":
            self.ids = set(values)
        else:
            self.filters[col] = set(values)
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, _n):
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def _match(self, row):
        for col, val in self.filters.items():
            if isinstance(val, set):
                if row.get(col) not in val:
                    return False
            elif row.get(col) != val:
                return False
        return self.ids is None or row["id"] in self.ids

    def execute(self):
        rows = [r for r in self.db.rows if self._match(r)]
        if self.op == "update":
            for r in rows:
                r.update(self.payload)
        return types.SimpleNamespace(data=[dict(r) for r in rows])


class FakeAlerts:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "alerts", name
        return _Q(self)


def row(i, source, text, status="open"):
    return {"id": i, "source": source, "issues": [text], "status": status}


class Keys(unittest.TestCase):
    def test_same_problem_at_another_time_is_the_same_key(self):
        a = ah.alert_key("performance", ["client 27: failed at 2026-11-14T08:00:01+00:00 HTTP 401"])
        b = ah.alert_key("performance", ["client 27: failed at 2026-11-15T09:30:00Z HTTP 401"])
        self.assertEqual(a, b)

    def test_another_client_or_status_is_another_key(self):
        base = ah.alert_key("performance", ["client 27: HTTP 401"])
        self.assertNotEqual(base, ah.alert_key("performance", ["client 28: HTTP 401"]))
        self.assertNotEqual(base, ah.alert_key("performance", ["client 27: HTTP 403"]))
        self.assertNotEqual(base, ah.alert_key("website_agent", ["client 27: HTTP 401"]))


class OpenDuplicate(unittest.TestCase):
    def test_an_open_alert_with_the_same_key_is_found(self):
        db = FakeAlerts([row(5, "lead_tracking", "client 27: no sales lead linked by client_id")])
        self.assertEqual(ah.open_duplicate(db, "lead_tracking", ["client 27: no sales lead linked by client_id"]), 5)

    def test_a_resolved_one_does_not_block_a_new_alert(self):
        db = FakeAlerts([row(5, "lead_tracking", "client 27: no sales lead linked", "resolved")])
        self.assertIsNone(ah.open_duplicate(db, "lead_tracking", ["client 27: no sales lead linked"]))

    def test_audit_sources_are_never_deduped(self):
        db = FakeAlerts([row(5, "operator", "tier2 clear_cache START")])
        self.assertIsNone(ah.open_duplicate(db, "operator", ["tier2 clear_cache START"]))

    def test_an_unreadable_table_stores_the_alert(self):
        class Broken:
            def table(self, _n):
                raise RuntimeError("down")
        self.assertIsNone(ah.open_duplicate(Broken(), "performance", ["client 27: x"]))

    def test_the_alert_sink_checks_before_inserting(self):
        src = read("agents/master_agent.py")
        self.assertLess(src.index("open_duplicate("), src.index('.insert({'))


class AutoResolve(unittest.TestCase):
    def setUp(self):
        self.db = FakeAlerts([
            row(1, "google_ads_health", "client 27: Google Ads connection needs reconnect — invalid_grant"),
            row(2, "google_ads_health", "client 270: Google Ads connection needs reconnect — invalid_grant"),
            row(3, "google_ads_agent", "client 27: campaign budget looks wrong"),
            row(4, "operator", "client 27: tier2 refresh_token OK needs reconnect"),
            row(5, "google_ads_agent", "client 27: Google Ads performance fetch failed: boom"),
        ])

    def test_only_this_clients_connection_alerts_close(self):
        spec = ah.CONNECTION_ALERTS["google_ads"]
        closed = ah.resolve_for_client(27, spec[0], spec[1], db=self.db)
        status = {r["id"]: r["status"] for r in self.db.rows}
        self.assertEqual(closed, 2)
        self.assertEqual(status, {1: "resolved", 2: "open", 3: "open", 4: "open", 5: "resolved"})

    def test_audit_rows_are_never_closed(self):
        self.assertEqual(ah.resolve_for_client(27, ["operator"], (), db=self.db), 0)

    def test_never_raises(self):
        class Broken:
            def table(self, _n):
                raise RuntimeError("down")
        self.assertEqual(ah.resolve_for_client(27, ["google_ads_health"], (), db=Broken()), 0)

    def test_the_health_recorders_call_it_on_the_ok_transition(self):
        for path, name in (("core/google_ads_health.py", '"google_ads"'), ("core/gtm_connection.py", '"gtm"'),
                           ("core/tiktok_grant.py", '"tiktok"')):
            self.assertIn(f"connection_works(client_id, {name})", read(path), path)


class NewestGrant(unittest.TestCase):
    def test_reconnect_updates_the_row_the_readers_read(self):
        src = read("agents/client_agent.py")
        body = src[src.index("def upsert_account"):src.index("def remove_accounts")]
        self.assertIn('.order("id", desc=True).limit(1)', body)


class Unauthorized(unittest.TestCase):
    def test_401_is_reconnect_on_the_card_and_in_the_health_record(self):
        perf = read("core/performance.py")
        body = perf[perf.index("def _classify_google_failure"):perf.index("def _tracking_card")]
        self.assertIn("or status == 401", body)
        self.assertIn('failure.get("http_status") == 401', read("core/gtm_connection.py"))


class AlertOnceRace(unittest.TestCase):
    def test_check_and_marker_are_under_one_lock(self):
        src = read("core/chat_fallback.py")
        body = src[src.index("def alert_once"):src.index("# ─── Capability gaps")]
        self.assertIn("with _ALERT_ONCE_LOCK:", body)
        self.assertLess(body.index("with _ALERT_ONCE_LOCK:"), body.index("_recently_alerted("))


if __name__ == "__main__":
    unittest.main()
