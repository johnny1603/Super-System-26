"""The account manager is the CLIENT's choice, and changeable (2026-10-31).

1. A client whose manager_persona is NULL — existing clients included — is
   offered the three managers on load until they pick; the default only
   renders, it is never written back.
2. Always switchable: /profile/ and the dashboard's personal area, the
   current one marked; a switch repaints the whole page (and other open
   tabs) from the one source, and the open thread keeps its history.
3. The warm one-line explanation sits next to every picker.
4. Staff see and set it from the Control Room.

Stdlib only. Run from the tests folder:  python -I -B test_manager_choice.py -v
"""
import os
import re
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

WHY_HE = "זה האדם שינהל את החשבון שלך וידבר איתך ביום-יום — אפשר להחליף בכל רגע."


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _js_function(html: str, name: str) -> str:
    start = html.index(f"function {name}(")
    depth, i = 0, html.index("{", start)
    while True:
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                return html[start:i + 1]
        i += 1


# ─── the client store, the DB write and the activity log ─────────────────────

CLIENTS, WRITES, ACTIVITY = {}, [], []


class _Query:
    def __init__(self):
        self.row = None

    def update(self, row):
        self.row = row
        return self

    def eq(self, col, value):
        self.client_id = value
        return self

    def execute(self):
        if DB_FAILS[0]:
            raise RuntimeError("column manager_persona does not exist")
        WRITES.append((self.client_id, dict(self.row)))
        CLIENTS.setdefault(self.client_id, {}).update(self.row)
        return types.SimpleNamespace(data=[])


DB_FAILS = [False]
_db = types.SimpleNamespace(table=lambda name: _Query())
client_agent = types.ModuleType("agents.client_agent")
client_agent.get_client = lambda cid: dict(CLIENTS.get(cid, {}))
client_agent._db = lambda: _db
client_agent.log_activity = lambda cid, agent, action, details=None, result=None: ACTIVITY.append(
    (cid, agent, action, details))
_saved = {n: sys.modules.get(n) for n in ("agents.client_agent", "core.manager_identity")}
sys.modules["agents.client_agent"] = client_agent
sys.modules.pop("core.manager_identity", None)

from core import manager_identity as mi   # noqa: E402


def tearDownModule():
    for name, module in _saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


class Base(unittest.TestCase):
    def setUp(self):
        CLIENTS.clear()
        WRITES.clear()
        ACTIVITY.clear()
        DB_FAILS[0] = False


# ─── 1. The default renders; it is never assigned ────────────────────────────

class NeverSilent(Base):
    def test_unchosen_client_renders_the_default_but_is_not_chosen(self):
        CLIENTS[7] = {"id": 7, "manager_persona": None, "owner_gender": "female"}
        view = mi.for_client_id(7)
        self.assertEqual(view["key"], "elinor")
        self.assertFalse(view["chosen"])
        self.assertEqual(WRITES, [])                      # reading never writes the default back

    def test_nothing_but_save_choice_writes_the_column(self):
        writers = []
        for folder in ("agents", "core"):
            for name in os.listdir(os.path.join(ROOT, folder)):
                if name.endswith(".py") and re.search(r"""["']manager_persona["']\s*:""", _src(folder, name)):
                    writers.append(f"{folder}/{name}")
        self.assertEqual(writers, ["core/manager_identity.py"])
        self.assertEqual(_src("core", "manager_identity.py").count('{"manager_persona": key}'), 1)

    def test_dashboard_offers_the_choice_to_every_unchosen_client(self):
        html = _src("dashboard", "client", "index.html")
        show = _js_function(html, "showManagerChoice")
        self.assertIn("const unchosen = !(manager && manager.chosen);", show)
        self.assertIn("!ADMIN_VIEW", show)                 # staff viewing as the client never get it
        self.assertIn("!managerChoiceDismissed()", show)
        self.assertIn("managerChoiceOverlay", show)
        self.assertIn("showManagerChoice(manager);", _js_function(html, "initManager"))
        # "later" hides it for this browser session only, never permanently
        self.assertIn("sessionStorage.setItem('uallak_manager_later', '1')", _js_function(html, "laterManagerChoice"))
        self.assertNotIn("localStorage.setItem('uallak_manager_later'", html)


# ─── 2. Changeable, logged, applied everywhere ───────────────────────────────

class Change(Base):
    def test_first_pick_and_later_switch_are_logged_with_who(self):
        CLIENTS[7] = {"id": 7, "manager_persona": None}
        self.assertTrue(mi.change(7, "nissim")["success"])
        self.assertTrue(mi.change(7, "elinor", by="staff")["success"])
        self.assertEqual(CLIENTS[7]["manager_persona"], "elinor")
        self.assertEqual([a[2] for a in ACTIVITY], ["manager_chosen", "manager_changed"])
        self.assertEqual(ACTIVITY[0][3], {"from": None, "to": "nissim", "by": "client"})
        self.assertEqual(ACTIVITY[1][3], {"from": "nissim", "to": "elinor", "by": "staff"})

    def test_unknown_key_and_unmigrated_column_fail_loudly(self):
        CLIENTS[7] = {"id": 7}
        self.assertEqual(mi.change(7, "dana")["code"], "ERR_MANAGER_UNKNOWN")
        DB_FAILS[0] = True
        self.assertEqual(mi.change(7, "nissim")["code"], "ERR_MANAGER_NOT_SAVED")
        self.assertEqual(ACTIVITY, [])

    def test_every_reader_sees_the_new_manager_at_once(self):
        CLIENTS[7] = {"id": 7, "manager_persona": "johnny"}
        mi.change(7, "elinor")
        self.assertEqual(mi.for_client_id(7)["key"], "elinor")             # emails, WhatsApp
        self.assertIn("Elinor", mi.prompt_line(mi.for_client(CLIENTS[7])))  # chat + morning briefing
        self.assertEqual(mi.signoff(mi.for_client_id(7, "he"), "he"), "אלינור — מנהלת הלקוח שלך ב-uallak")
        # Nobody keeps a copy: no module caches the resolved identity
        for name in ("support_agent.py", "engagement_agent.py", "interview_agent.py"):
            self.assertNotRegex(_src("agents", name), r"_manager_cache|lru_cache\(.*\)\s*\n\s*def .*manager")

    def test_endpoints(self):
        api = _src("core", "api_server.py")
        for route in ('@app.get("/api/client/manager")', '@app.post("/api/client/manager")',
                      '@app.get("/api/admin/clients/{client_id}/manager")',
                      '@app.post("/api/admin/clients/{client_id}/manager")'):
            self.assertIn(route, api)
        admin_post = api[api.index('@app.post("/api/admin/clients/{client_id}/manager")'):]
        admin_post = admin_post[:admin_post.index("\n@app.")]
        self.assertIn("_require_admin(request)", admin_post)
        self.assertIn('"staff"', admin_post)
        client_post = api[api.index('@app.post("/api/client/manager")'):]
        client_post = client_post[:client_post.index("\n@app.")]
        self.assertIn("_require_session(request)", client_post)

    def test_dashboard_switch_is_saved_first_then_repaints_everything(self):
        html = _src("dashboard", "client", "index.html")
        choose = _js_function(html, "chooseManager")
        self.assertIn("fetch('/api/client/manager'", choose)
        self.assertLess(choose.index("await fetch("), choose.index("applyManager(saved.manager)"))
        self.assertIn("manager_save_failed", choose)
        self.assertIn("localStorage.setItem('uallak_manager_changed'", choose)
        # Cards already in the open thread get the new byline; nothing re-fetches history
        refresh = _js_function(html, "refreshWindowIdentities")
        self.assertIn(".chat-action > .by", refresh)
        self.assertNotIn("history", refresh)
        self.assertIn("refreshWindowIdentities();", _js_function(html, "applyManager"))
        self.assertIn("e.key === 'uallak_manager_changed'", html)

    def test_every_picker_marks_the_current_one(self):
        html = _src("dashboard", "client", "index.html")
        render = _js_function(html, "renderManagerPick")
        self.assertIn("document.querySelectorAll('[data-manager-choices]')", render)
        self.assertIn("aria-pressed", render)
        self.assertIn("MANAGER.chosen ? MANAGER.key : ''", render)   # a default is never marked as chosen
        boxes = re.findall(r"<div [^>]*data-manager-choices[^>]*>", html)
        self.assertEqual(len(boxes), 3)    # first-visit choice, chat launcher, personal area
        personal = html[html.index('id="viewPersonal"'):html.index("<!-- /viewPersonal -->")]
        self.assertIn('id="managerSettings"', personal)

    def test_profile_settings_page_switch(self):
        page = _src("dashboard", "profile", "index.html")
        self.assertIn('id="managerRow"', page)
        choose = _js_function(page, "chooseManager")
        self.assertIn("fetch('/api/client/manager'", choose)
        self.assertIn("localStorage.setItem('uallak_manager_changed'", choose)
        self.assertIn('aria-pressed="${on}"', _js_function(page, "renderManagerRow"))
        self.assertIn("loadManager();", page)
        for key in mi.ORDER:                       # no copy of the names on this page either
            for nm in mi.MANAGERS[key]["name"].values():
                self.assertNotIn(nm, re.sub(r"<!--.*?-->", "", page, flags=re.S))


# ─── 3. The warm line ────────────────────────────────────────────────────────

class WarmLine(unittest.TestCase):
    def test_next_to_every_picker_in_five_languages(self):
        html = _src("dashboard", "client", "index.html")
        self.assertEqual(html.count(f'data-i18n="manager_choice_why">{WHY_HE}<'), 3)
        line = re.search(r"^  manager_choice_why: \{(.*)\},$", html, re.M).group(1)
        for lang in ("he:", "en:", "fr:", "ar:", "ru:"):
            self.assertIn(lang, line)
        page = _src("dashboard", "profile", "index.html")
        self.assertIn(f'data-i18n="manager_why">{WHY_HE}<', page)
        for key in ("manager_choice_title", "manager_choice_later", "manager_settings_title", "manager_current",
                    "manager_not_chosen", "manager_save_failed"):
            row = re.search(rf"^  {key}: \{{(.*)\}},$", html, re.M)
            self.assertIsNotNone(row, key)
            for lang in ("he:", "en:", "fr:", "ar:", "ru:"):
                self.assertIn(lang, row.group(1), key)


# ─── 4. Control Room ─────────────────────────────────────────────────────────

class ControlRoom(unittest.TestCase):
    def test_staff_see_and_set_it(self):
        admin = _src("dashboard", "admin", "index.html")
        self.assertIn('id="crManager"', admin)
        self.assertIn("crLoadManager(clientId);", _js_function(admin, "openControlRoom"))
        load = _js_function(admin, "crLoadManager")
        self.assertIn("/api/admin/clients/${Number(clientId)}/manager", load)
        self.assertIn("m.chosen", load)                        # chosen vs. only a default, said plainly
        setter = _js_function(admin, "crSetManager")
        self.assertIn("method: 'POST'", setter)
        self.assertIn("JSON.stringify({ manager: key })", setter)


if __name__ == "__main__":
    unittest.main()
