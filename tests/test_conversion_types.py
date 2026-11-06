"""Per-client conversion types (2026-11-02, core/conversion_types).

- The setting: known types only, existing clients default to form_submit,
  the write touches only that client's row; the migration is idempotent and
  changes Shin Sekai (27) and no one else.
- The selection card: the interview's "how do customers reach you" becomes a
  set_conversion_types card (never saved silently); the client's click saves it.
- Tracking + Performance: the chat card offers only what isn't live yet; the
  page shows the client's own types with Hebrew labels, store sales first, and
  says a click is an indication, not a conversation.
(The Tag Manager triggers per type: tests/test_google_scope_flows.py.)

Stdlib only. Run:  python -I -B tests/test_conversion_types.py -v
"""
import os
import re
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
if "anthropic" not in sys.modules:
    _anthropic = types.ModuleType("anthropic")
    _anthropic.Anthropic = lambda *a, **k: None
    sys.modules["anthropic"] = _anthropic

from test_agent_actions import CLIENTS, STATE_READY, ActionTestCase  # noqa: E402
from core import agent_actions as aa  # noqa: E402
from core import conversion_types as ct  # noqa: E402


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class ClientsTable:
    """Just the clients rows conversion_types reads and writes."""

    def __init__(self, rows):
        self.rows, self.updates, self.fail = rows, [], False
        self._op = None

    def table(self, name):
        assert name == "clients", name
        self._op, self._filters, self._values = "select", {}, None
        return self

    def select(self, cols):
        if self.fail:
            raise RuntimeError("column conversion_types does not exist")
        return self

    def update(self, values):
        self._op, self._values = "update", values
        return self

    def eq(self, col, value):
        self._filters[col] = value
        return self

    def limit(self, n):
        return self

    def execute(self):
        match = [r for r in self.rows if all(r.get(k) == v for k, v in self._filters.items())]
        if self._op == "update":
            if self.fail:
                raise RuntimeError("column conversion_types does not exist")
            for r in match:
                r.update(self._values)
            self.updates.append((dict(self._filters), self._values))
        return type("R", (), {"data": [dict(r) for r in match]})()


class Setting(unittest.TestCase):
    def setUp(self):
        self.db = ClientsTable([{"id": 27, "conversion_types": ["whatsapp_click", "store_sales"],
                                 "conversion_types_confirmed_at": "2026-11-02T10:00:00+00:00"},
                                {"id": 40, "conversion_types": ["form_submit"], "conversion_types_confirmed_at": None},
                                {"id": 41}])
        self.saved = ct._db
        ct._db = lambda: self.db

    def tearDown(self):
        ct._db = self.saved

    def test_clean(self):
        self.assertEqual(ct.clean(["phone_click", "FORM_SUBMIT", "nope", "phone_click"]), ["form_submit", "phone_click"])
        self.assertEqual(ct.clean("store_sales, whatsapp_click"), ["whatsapp_click", "store_sales"])
        with self.assertRaises(ValueError):
            ct.clean(["nope"])
        with self.assertRaises(ValueError):
            ct.clean([])

    def test_reads(self):
        self.assertEqual(ct.get(27)["types"], ["whatsapp_click", "store_sales"])
        self.assertTrue(ct.get(27)["confirmed"])
        self.assertEqual(ct.get(40), {"types": ["form_submit"], "confirmed": False, "stored": True})
        self.assertEqual(ct.get(41)["types"], ["form_submit"])          # no value yet: the default

    def test_before_the_migration_everything_is_form_submit(self):
        self.db.fail = True
        self.assertEqual(ct.get(27), {"types": ["form_submit"], "confirmed": False, "stored": False})
        with self.assertRaises(RuntimeError):
            ct.set_types(27, ["phone_click"])

    def test_write_touches_only_that_client(self):
        result = ct.set_types(40, ["whatsapp_click", "phone_click"])
        self.assertEqual(result["labels"], ["פניות בוואטסאפ", "שיחות טלפון"])
        self.assertEqual(self.db.updates[0][0], {"id": 40})
        self.assertEqual(self.db.rows[0]["conversion_types"], ["whatsapp_click", "store_sales"])   # 27 untouched
        self.assertTrue(self.db.rows[1]["conversion_types_confirmed_at"])

    def test_hebrew_labels(self):
        self.assertEqual(ct.LABELS_HE, {"form_submit": "פניות מטפסים", "whatsapp_click": "פניות בוואטסאפ",
                                        "phone_click": "שיחות טלפון", "store_sales": "מכירות"})
        self.assertIn("לא אישור שהשיחה התקיימה", ct.CLICK_NOTE_HE)

    def test_from_channels(self):
        self.assertEqual(ct.from_channels(["store", "whatsapp", "carrier pigeon"]), ["whatsapp_click", "store_sales"])
        self.assertEqual(ct.from_channels([]), [])


class Migration(unittest.TestCase):
    def setUp(self):
        self.sql = _src("migrations", "2026-11-02-store-conversions.sql")
        self.body = "\n".join(l for l in self.sql.splitlines() if not l.strip().startswith("--"))

    def test_idempotent(self):
        self.assertIn("add column if not exists conversion_types text[] not null default '{form_submit}'", self.body)
        self.assertIn("add column if not exists conversion_types_confirmed_at", self.body)
        self.assertIn("on conflict (key) do nothing", self.body)

    def test_only_shin_sekai_is_set_and_never_overwritten(self):
        updates = re.findall(r"update clients.*?;", self.body, re.DOTALL)
        self.assertEqual(len(updates), 1)
        # Shin Sekai is store_sales ONLY (2026-11-07): a re-run never re-adds a lead type
        self.assertIn("'{store_sales}'", updates[0])
        for lead in ("whatsapp_click", "phone_click", "form_submit"):
            self.assertNotIn(lead, updates[0])
        self.assertIn("where id = 27", updates[0])
        self.assertIn("conversion_types_confirmed_at is null", updates[0])

    def test_no_migration_ever_gives_27_a_lead_type(self):
        folder = os.path.join(ROOT, "migrations")
        for name in sorted(os.listdir(folder)):
            body = "\n".join(l for l in _src("migrations", name).splitlines() if not l.strip().startswith("--"))
            for stmt in re.findall(r"update clients.*?;", body, re.DOTALL | re.IGNORECASE):
                if re.search(r"\bid\s*=\s*27\b", stmt):
                    for lead in ("whatsapp_click", "phone_click", "form_submit"):
                        self.assertNotIn(lead, stmt, name)


class SelectionCard(ActionTestCase):
    def setUp(self):
        super().setUp()
        self.saved_ct = (ct.get, ct.set_types)
        self.stored = []
        ct.get = lambda cid: {"types": ["form_submit"], "confirmed": False}
        ct.set_types = lambda cid, types: self.stored.append((cid, types)) or {"types": types}

    def tearDown(self):
        ct.get, ct.set_types = self.saved_ct
        super().tearDown()

    def test_card_shows_the_hebrew_labels_and_saves_only_on_the_click(self):
        result = self.propose(27, "client_chat", "set_conversion_types",
                              {"types": ["whatsapp_click", "store_sales"]})
        card = result["card"]
        self.assertIn("פניות בוואטסאפ, מכירות", card["what"])
        self.assertEqual(self.stored, [])                                 # nothing saved yet
        decided = aa.decide(card["id"], "client", 27, "approve")
        self.assertEqual(decided["status"], "done")
        self.assertEqual(self.stored, [(27, ["whatsapp_click", "store_sales"])])

    def test_unknown_types_are_refused(self):
        self.assertEqual(self.propose(27, "client_chat", "set_conversion_types", {"types": ["guess"]})["error"],
                         "invalid_params")

    def test_interview_proposes_the_card_from_what_the_client_said(self):
        from agents import interview_agent
        card = interview_agent.conversion_card(27, ["whatsapp", "store"])
        self.assertEqual(card["action"], "set_conversion_types")
        self.assertEqual(self.rows()[-1]["params"], {"types": ["whatsapp_click", "store_sales"]})
        self.assertEqual(self.stored, [])                                 # the click decides

    def test_interview_never_guesses(self):
        from agents import interview_agent
        self.assertIsNone(interview_agent.conversion_card(27, []))
        self.assertIsNone(interview_agent.conversion_card(27, None))
        ct.get = lambda cid: {"types": ["phone_click"], "confirmed": True}
        self.assertIsNone(interview_agent.conversion_card(27, ["whatsapp"]))   # already chosen
        self.assertEqual(self.rows(), [])

    def test_prompts_ask_and_never_guess(self):
        self.assertIn("HOW CUSTOMERS USUALLY REACH THEM", _src("agents", "interview_agent.py"))
        self.assertIn("set_conversion_types with exactly what they said — never guess it", _src("agents", "support_agent.py"))


class TrackingCard(unittest.TestCase):
    def test_offered_only_while_a_chosen_type_is_not_live(self):
        base = {"connected": True, "needs_reconnect": False}
        self.assertTrue(aa._conversion_setup_possible({"tracking": {**base, "conversion_types_not_live": ["phone_click"]}}, {}))
        self.assertFalse(aa._conversion_setup_possible({"tracking": {**base, "conversion_types_not_live": []}}, {}))
        # unknown live state: the old rule (lead event live?)
        self.assertTrue(aa._conversion_setup_possible({"tracking": {**base, "lead_event_live": False}}, {}))
        self.assertFalse(aa._conversion_setup_possible({"tracking": {**base, "lead_event_live": True}}, {}))

    def test_unreadable_choice_gets_its_own_refusal_line(self):
        self.assertEqual(aa._GTM_REFUSAL_CODES["no_gtm_types"], "no_gtm_types")
        self.assertIn("no_gtm_types", aa.ACTIONS["configure_conversion"]["refusals"])

    def test_unreadable_choice_offers_no_card(self):
        base = {"connected": True, "needs_reconnect": False, "lead_event_live": False}
        self.assertFalse(aa._conversion_setup_possible(
            {"tracking": {**base, "conversion_types": ["form_submit"], "conversion_types_readable": False}}, {}))


class PerformancePage(unittest.TestCase):
    def setUp(self):
        from core import performance
        self.p = performance
        self.saved = (ct.get, performance._store_sales)
        performance._store_sales = lambda cid: {"source": "store", "connected": True, "windows": [
            {"days": 7, "revenue": 320.0, "orders": 3, "average_order_value": 113.33, "currency": "ILS",
             "complete": True}]}

    def tearDown(self):
        ct.get, self.p._store_sales = self.saved

    def test_store_client_gets_sales_first_and_the_click_note(self):
        ct.get = lambda cid: {"types": ["whatsapp_click", "store_sales"], "confirmed": True}
        block = self.p._conversions_block(27, {"rows": []}, [{"whatsapp": 4, "calls": 1}, {"whatsapp": 2}])
        self.assertEqual(block["primary"], "store_sales")
        self.assertEqual([m["label"] for m in block["metrics"]], ["פניות בוואטסאפ", "מכירות"])
        self.assertEqual(block["metrics"][0]["windows"], [{"days": 30, "count": 6}])
        self.assertEqual(block["metrics"][1]["windows"][0]["revenue"], 320.0)
        self.assertIn("לא אישור שהשיחה התקיימה", block["click_note"])

    def test_form_client_is_unchanged_leads(self):
        from datetime import date
        ct.get = lambda cid: {"types": ["form_submit"], "confirmed": False}
        rows = [{"created_at": date.today().isoformat() + "T08:00:00"}]
        block = self.p._conversions_block(40, {"rows": rows}, [])
        self.assertEqual(block["primary"], "form_submit")
        self.assertEqual(block["metrics"][0]["windows"][0], {"days": 7, "count": 1})
        self.assertEqual(block["click_note"], "")

    def test_dashboard_renders_labels_by_type(self):
        html = _src("dashboard", "client", "index.html")
        for kind, label in ct.LABELS_HE.items():
            self.assertIn(f"conv_type_{kind}: {{ he:'{label}'", html)
        self.assertIn("renderConversions(conv)", html)


if __name__ == "__main__":
    unittest.main()
