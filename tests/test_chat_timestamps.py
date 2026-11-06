"""Chat timestamps (2026-11-07, docs/CHAT_TIMESTAMPS.md).

Stored times are UTC; the dashboard shows them in the client's zone
(clients.timezone, else the browser's) through ONE module,
dashboard/assets/chat-time.js. Its real code runs here under Node (the
browser's Intl, IANA zones with their DST rules) — including the EXISTING
conversation download function, taken out of the dashboard page and run with
stand-ins for the browser, so "the download carries the times" is tested on
the code that ships.

Node is preinstalled on the CI runner (ubuntu-latest); without it the Node
tests are skipped with a reason, the static and server checks still run.

Run:  python -I -B tests/test_chat_timestamps.py -v
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
MODULE = os.path.join(ROOT, "dashboard", "assets", "chat-time.js")
NODE = shutil.which("node")


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8-sig") as f:
        return f.read()


DASH = _src("dashboard", "client", "index.html")


def _js_function(name: str) -> str:
    start = DASH.index(name)
    return DASH[start:DASH.index("\n}\n", start) + 2]


def node(script: str):
    """Run `script` with T = chat-time.js; it prints one JSON value."""
    prelude = f"const T = require({json.dumps(MODULE)});\n"
    out = subprocess.run([NODE, "-e", prelude + script], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


@unittest.skipUnless(NODE, "node is not installed — the browser module can't run here")
class ZoneConversion(unittest.TestCase):
    def test_israel_dst_start_2026(self):
        # Friday 27/03/2026 02:00 IST → 03:00 IDT (UTC+2 → UTC+3)
        got = node("console.log(JSON.stringify(["
                   "T.full('2026-03-26T23:59:59Z', 'Asia/Jerusalem'),"
                   "T.full('2026-03-27T00:00:00Z', 'Asia/Jerusalem'),"
                   "T.time('2026-03-27T00:30:00Z', 'Asia/Jerusalem')]))")
        self.assertEqual(got, ["27/03/2026 01:59:59", "27/03/2026 03:00:00", "03:30"])

    def test_israel_dst_end_2026_the_repeated_hour(self):
        # Sunday 25/10/2026 02:00 IDT → 01:00 IST: 01:30 happens twice
        got = node("console.log(JSON.stringify(["
                   "T.full('2026-10-24T22:30:00Z', 'Asia/Jerusalem'),"
                   "T.full('2026-10-24T23:30:00Z', 'Asia/Jerusalem'),"
                   "T.dayKey('2026-10-24T22:30:00Z', 'Asia/Jerusalem'),"
                   "T.time('2026-10-24T20:59:00Z', 'Asia/Jerusalem')]))")
        self.assertEqual(got, ["25/10/2026 01:30:00", "25/10/2026 01:30:00", "2026-10-25", "23:59"])

    def test_another_zone_uses_its_own_rules(self):
        # New York springs forward on 08/03/2026; Israel only on 27/03
        got = node("console.log(JSON.stringify(["
                   "T.time('2026-03-08T06:59:00Z', 'America/New_York'),"
                   "T.time('2026-03-08T07:00:00Z', 'America/New_York'),"
                   "T.time('2026-03-08T07:00:00Z', 'Asia/Jerusalem'),"
                   "T.time('2026-03-08T07:00:00Z', 'UTC')]))")
        self.assertEqual(got, ["01:59", "03:00", "09:00", "07:00"])

    def test_24h_format_and_the_download_format(self):
        got = node("console.log(JSON.stringify(["
                   "T.time('2026-11-07T22:05:09Z', 'Asia/Jerusalem'),"
                   "T.time('2026-11-07T10:00:00Z', 'Asia/Jerusalem'),"
                   "T.stamp('2026-11-07T22:05:09Z', 'Asia/Jerusalem'),"
                   "T.date('2026-11-07T22:05:09Z', 'Asia/Jerusalem')]))")
        self.assertEqual(got, ["00:05", "12:00", "08/11/2026 00:05", "08/11/2026"])

    def test_supabase_timestamps_parse(self):
        got = node("console.log(JSON.stringify(["
                   "T.full('2026-11-07T08:00:00.123456+00:00', 'UTC'),"
                   "T.full('2026-11-07 08:00:00', 'UTC'),"           # no offset = UTC
                   "T.full('2026-11-07T10:00:00+02:00', 'UTC'),"
                   "T.toDate('garbage'), T.toDate(''), T.time('none', 'UTC')]))")
        self.assertEqual(got, ["07/11/2026 08:00:00", "07/11/2026 08:00:00", "07/11/2026 08:00:00",
                               None, None, ""])

    def test_zone_choice_and_never_a_fixed_offset(self):
        got = node("console.log(JSON.stringify(["
                   "T.resolveZone('Asia/Jerusalem', 'Europe/London'),"
                   "T.resolveZone('', 'Europe/London'),"
                   "T.resolveZone('Mars/Olympus', 'Europe/London'),"
                   "T.resolveZone('+03:00', 'Europe/London'),"
                   "T.resolveZone('', ''),"
                   "T.validZone('America/Argentina/Buenos_Aires'), T.validZone('GMT+3'), T.validZone('UTC')]))")
        self.assertEqual(got, ["Asia/Jerusalem", "Europe/London", "Europe/London", "Europe/London",
                               "Asia/Jerusalem", True, False, True])


@unittest.skipUnless(NODE, "node is not installed — the browser module can't run here")
class DaySeparators(unittest.TestCase):
    LABELS = "{today: 'היום', yesterday: 'אתמול'}"

    def test_today_yesterday_then_the_date_in_the_clients_zone(self):
        now = "new Date('2026-03-27T10:00:00Z')"
        got = node("console.log(JSON.stringify(["
                   f"T.dayLabel('2026-03-27T08:00:00Z', 'Asia/Jerusalem', {now}, {self.LABELS}),"
                   # 22:30 UTC on the 26th is already 00:30 on the 27th in Israel
                   f"T.dayLabel('2026-03-26T22:30:00Z', 'Asia/Jerusalem', {now}, {self.LABELS}),"
                   f"T.dayLabel('2026-03-26T21:30:00Z', 'Asia/Jerusalem', {now}, {self.LABELS}),"
                   f"T.dayLabel('2026-03-25T12:00:00Z', 'Asia/Jerusalem', {now}, {self.LABELS}),"
                   # the same instant is still "yesterday" in Los Angeles
                   f"T.dayLabel('2026-03-27T03:00:00Z', 'America/Los_Angeles', {now}, {self.LABELS})]))")
        self.assertEqual(got, ["היום", "היום", "אתמול", "25/03/2026", "אתמול"])

    def test_yesterday_across_the_dst_night_and_month_and_year_edges(self):
        got = node("console.log(JSON.stringify(["
                   "T.previousDay('2026-03-28'), T.previousDay('2026-03-01'), T.previousDay('2026-01-01'),"
                   "T.previousDay('2028-03-01'),"
                   # the morning after the clocks went back: "yesterday" is the 25th
                   f"T.dayLabel('2026-10-24T22:30:00Z', 'Asia/Jerusalem', new Date('2026-10-26T06:00:00Z'), {self.LABELS})]))")
        self.assertEqual(got, ["2026-03-27", "2026-02-28", "2025-12-31", "2028-02-29", "אתמול"])

    def test_separator_positions(self):
        got = node("console.log(JSON.stringify(T.separatorIndexes(["
                   "{at: '2026-03-26T20:00:00Z'}, {at: '2026-03-26T21:59:00Z'},"
                   "{at: '2026-03-26T22:00:00Z'},"     # midnight in Israel (UTC+2): a new day
                   "{at: null}, {at: '2026-03-27T09:00:00Z'}, {at: '2026-03-29T09:00:00Z'}], 'Asia/Jerusalem')))")
        self.assertEqual(got, [0, 2, 5])


@unittest.skipUnless(NODE, "node is not installed — the browser module can't run here")
class ExistingDownload(unittest.TestCase):
    """downloadWindowConversation as it ships, run with browser stand-ins."""

    def run_download(self, history, tz):
        fn = _js_function("async function downloadWindowConversation(")
        zone = _js_function("function chatZone()")
        script = f"""
const uallakChatTime = T;
var CHAT_TZ_SETTING = {json.dumps(tz)};
{zone}
const chatData = {{ name: 'Shin Sekai' }};
const uallakI18n = {{ t: k => ({{chat_dl_me: 'אני', chat_dl_team: 'uallak', chat_dl_empty: '(ריק)', r: 'מדיה'}})[k] || k,
                      locale: () => 'he-IL' }};
const personaMeta = () => ({{ name: 'ליאור', roleKey: 'r' }});
let saved = null;
global.Blob = function (parts) {{ saved = parts.join(''); }};
global.URL = {{ createObjectURL: () => 'blob:x', revokeObjectURL: () => {{}} }};
global.document = {{ body: {{ appendChild: () => {{}} }},
                     createElement: () => ({{ click: () => {{}}, remove: () => {{}} }}) }};
global.fetch = async () => ({{ json: async () => ({{ success: true, history: {json.dumps(history)} }}) }});
{fn}
downloadWindowConversation('media').then(() => console.log(JSON.stringify(saved)));
"""
        return node(script)

    def test_every_message_carries_its_local_time(self):
        text = self.run_download([
            {"direction": "inbound", "content": "שלום", "created_at": "2026-03-26T21:59:00+00:00"},
            {"direction": "outbound", "content": "היי!", "created_at": "2026-03-27T00:30:00.5+00:00"},
        ], "Asia/Jerusalem")
        self.assertIn("[26/03/2026 23:59] אני:\nשלום", text)
        self.assertIn("[27/03/2026 03:30] uallak:\nהיי!", text)        # after the DST jump
        self.assertIn("(Asia/Jerusalem)", text.splitlines()[1])
        self.assertNotIn("[]", text)

    def test_the_clients_zone_wins_and_the_format_is_the_same(self):
        text = self.run_download([{"direction": "inbound", "content": "x",
                                   "created_at": "2026-11-07T08:00:00+00:00"}], "America/New_York")
        self.assertIn("[07/11/2026 03:00] אני:", text)

    def test_format_and_permissions_are_unchanged(self):
        fn = _js_function("async function downloadWindowConversation(")
        self.assertIn("text/plain;charset=utf-8", fn)                  # still the same TXT file
        self.assertIn("/api/client-chat/history?scope=all&persona=", fn)   # same session-gated read
        self.assertNotIn("toLocaleString", fn)


class Dashboard(unittest.TestCase):
    def test_module_loaded_and_every_message_stamped(self):
        self.assertIn('<script src="/assets/chat-time.js"></script>', DASH)
        append = _js_function("function appendMsg(")
        self.assertIn("stampMsg(msg, at || new Date())", append)
        self.assertIn("paintChatDays(msgsEl)", append)
        history = _js_function("async function loadWindowHistory(")
        self.assertIn("entry.created_at || 'none'", history)           # stored time, never "now"
        prev = _js_function("async function loadPrevInWindow(")
        self.assertIn("stampMsg(msg, entry.created_at", prev)
        self.assertIn("paintChatDays(w.msgsEl)", prev)

    def test_hover_long_press_and_tap_show_the_full_time(self):
        el = _js_function("function chatTimeEl(")
        self.assertIn("tm.title = long", el)
        self.assertIn("'pointerdown'", el)
        self.assertIn("T.full(at, tz)", el)
        self.assertIn("tm.dir = 'ltr'", el)

    def test_cards_show_created_decided_and_who(self):
        card = _js_function("function appendActionCard(")
        self.assertIn("paintActionWhen(el, a)", card)
        when = _js_function("function paintActionWhen(")
        for key in ("action_created", "action_decided", "action_by_client", "action_by_team"):
            self.assertIn(key, when)
        self.assertIn("textContent", when)
        self.assertNotIn("innerHTML", when)

    def test_texts_in_every_language(self):
        for key, he in (("chat_today", "היום"), ("chat_yesterday", "אתמול"), ("action_created", "נוצר {t}"),
                        ("action_decided", "הוחלט {t}"), ("action_by_client", "על ידך"),
                        ("action_by_team", "על ידי הצוות"), ("action_by_system", "על ידי המערכת")):
            line = re.search(r"^  %s: \{(.*)\},$" % key, DASH, re.M)
            self.assertIsNotNone(line, key)
            self.assertIn("he:'%s'" % he, line.group(1))
            for lang in ("en:", "fr:", "ar:", "ru:"):
                self.assertIn(lang, line.group(1))


class Server(unittest.TestCase):
    def test_the_dashboard_sends_a_checked_zone(self):
        api = _src("core", "api_server.py")
        tree = ast.parse(api)
        ns = {"re": re}
        for name in ("_TZ_NAME",):
            node_ = next(n for n in tree.body if isinstance(n, ast.Assign)
                         and any(getattr(t, "id", "") == name for t in n.targets))
            exec(compile(ast.get_source_segment(api, node_), "api_server", "exec"), ns)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_client_timezone")
        exec(compile(ast.get_source_segment(api, fn), "api_server", "exec"), ns)
        tz = ns["_client_timezone"]
        self.assertEqual(tz({"timezone": "Asia/Jerusalem"}), "Asia/Jerusalem")
        self.assertEqual(tz({"timezone": "America/Argentina/Buenos_Aires"}), "America/Argentina/Buenos_Aires")
        self.assertEqual(tz({"timezone": "UTC"}), "UTC")
        for bad in ("+03:00", "<script>", "Asia/../x", "", None, "a" * 80 + "/b"):
            self.assertEqual(tz({"timezone": bad}), "", bad)
        self.assertEqual(tz({}), "")                                     # before the migration
        self.assertIn('"timezone": _client_timezone(client)', api)

    def test_cards_carry_decided_at_and_who(self):
        from core import agent_actions as aa
        row = {"id": 1, "action": "connect_tiktok", "status": "done", "client_id": 7, "approver": "client",
               "created_at": "2026-11-07T08:00:00+00:00", "decided_at": "2026-11-07T08:05:00+00:00",
               "decided_by": "staff"}
        card = aa.card(row, "client")
        self.assertEqual((card["created_at"], card["decided_at"], card["decided_by"]),
                         ("2026-11-07T08:00:00+00:00", "2026-11-07T08:05:00+00:00", "team"))
        self.assertEqual(aa.card({**row, "decided_by": "client"}, "client")["decided_by"], "client")
        self.assertEqual(aa.card({**row, "decided_by": "admin"}, "client")["decided_by"], "team")
        self.assertEqual(aa.card({**row, "decided_by": "system"}, "client")["decided_by"], "system")
        self.assertEqual(aa.card({**row, "decided_at": None}, "client")["decided_by"], "")

    def test_the_migration(self):
        sql = _src("migrations", "2026-11-07-chat-timezone.sql")
        self.assertIn("alter table clients add column if not exists timezone text not null default 'Asia/Jerusalem';", sql)
        # created_at is never added WITH a default in one step (that would backfill old rows)
        self.assertIn("add column if not exists created_at timestamptz;", sql)
        self.assertIn("alter column created_at set default now();", sql)


if __name__ == "__main__":
    unittest.main()
