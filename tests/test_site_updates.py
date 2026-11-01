"""Plugin/theme updates through the website agent's cards (core/site_updates +
core/agent_actions update_site_items / update_site_items_without_backup).

- status view: installed vs available, active state, PHP / WordPress
  incompatibility flags, premium plugins never offered, the plain offer line
- flow: one item at a time, a verified backup BEFORE each update, health
  check after (home + key pages + checkout when the store is active + WordPress
  loads), every item logged (who, what, from -> to, backup ref, result)
- backup gating: no verified backup -> nothing updated, the rest goes to a
  separate code-only card that says there is no backup
- rollback: a failed health check restores that item's backup, alerts admin,
  and the client reads plain Hebrew
- WordPress core is never updated; commands are built from validated slugs only

Stdlib only; the site, the directory and the host are fakes.
Run:  python tests/test_site_updates.py -v
"""
import os
import sys
import types
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import site_updates as su   # noqa: E402

SITE = "https://shop.example.com"
PROBE_OK = "wp eval 'echo \"ok\";'"


class FakeSite:
    """The client's WordPress (REST + public pages) and its host."""

    def __init__(self):
        self.plugins = {
            "wordpress-seo/wp-seo": {"plugin": "wordpress-seo/wp-seo", "status": "active", "name": "Yoast SEO",
                                     "version": "22.0"},
            "woocommerce/woocommerce": {"plugin": "woocommerce/woocommerce", "status": "active",
                                        "name": "WooCommerce", "version": "9.0.0"},
            "premium-x/premium-x": {"plugin": "premium-x/premium-x", "status": "inactive", "name": "Premium X",
                                    "version": "1.0"},
        }
        self.themes = {"astra": {"stylesheet": "astra", "status": "active", "name": {"raw": "Astra"},
                                 "version": "4.0.0"}}
        self.directory = {("plugin", "wordpress-seo"): {"version": "22.1", "requires_wp": "6.4", "requires_php": "7.4"},
                          ("plugin", "woocommerce"): {"version": "9.4.0", "requires_wp": "6.5", "requires_php": "8.3"},
                          ("theme", "astra"): {"version": "4.1.0", "requires_wp": "6.0", "requires_php": "7.4"}}
        self.broken = False
        self.break_on = set()
        self.unfixable = False
        self.backup_fails = False
        self.backup_unlisted = False
        self.versions = {}
        self.calls = []
        self.fetched = []

    # host API (core/instawp_service shape)
    def run_cmd(self, site_id, command, timeout_seconds=120):
        self.calls.append(("cmd", command))
        if command == su.VERSION_PROBE:
            return {"output": "8.1.2 6.6.1", "exit_code": 0}
        if command == PROBE_OK:
            return {"output": "" if self.broken else "ok", "exit_code": 255 if self.broken else 0}
        parts = command.split()
        assert parts[:1] == ["wp"] and parts[2] == "update", command
        kind, slug = parts[1], parts[3]
        rows = self.plugins if kind == "plugin" else self.themes
        for row in rows.values():
            if su.slug_of(kind, row) == slug:
                row["version"] = self.directory[(kind, slug)]["version"]
        if slug in self.break_on:
            self.broken = True
        return {"output": "Success: Updated 1 of 1.", "exit_code": 0}

    def create_site_version(self, site_id):
        self.calls.append(("backup",))
        if self.backup_fails:
            raise RuntimeError("500: version create failed")
        vid = 900 + len(self.versions)
        self.versions[vid] = ({k: dict(v) for k, v in self.plugins.items()},
                              {k: dict(v) for k, v in self.themes.items()}, self.broken)
        return {"id": vid, "task_id": f"t{vid}"}

    def wait_for_task(self, task_id, max_seconds=600, sleep=None):
        return {"status": "completed"}

    def list_site_versions(self, site_id):
        return [] if self.backup_unlisted else [{"id": vid} for vid in self.versions]

    def name_site_version(self, vid, name):
        pass

    def restore_site_version(self, site_id, vid):
        self.calls.append(("restore", str(vid)))
        plugins, themes, broken = self.versions[int(vid)]
        self.plugins = {k: dict(v) for k, v in plugins.items()}
        self.themes = {k: dict(v) for k, v in themes.items()}
        self.broken = self.unfixable or broken
        return {"task_id": "r1"}

    # REST + public pages
    def wp_get(self, creds, path, params=None):
        if path == "wp/v2/plugins":
            return [dict(v) for v in self.plugins.values()]
        if path == "wp/v2/themes":
            return [dict(v) for v in self.themes.values()]
        if path == "wp/v2/pages":
            if (params or {}).get("slug") == "checkout":
                return [{"link": f"{SITE}/checkout/"}]
            return [{"link": f"{SITE}/about/"}, {"link": "https://elsewhere.example/x"}]
        raise AssertionError(path)

    def http_get(self, url):
        self.fetched.append(url.split("?")[0])
        if self.broken:
            return 500, "<p>There has been a critical error on this website.</p>"
        return 200, '<meta name="generator" content="WordPress 6.6.1"><p>hello</p>'


class Base(unittest.TestCase):
    def setUp(self):
        self.site = FakeSite()
        self.logs, self.alerts = [], []
        self.provisioned = [{"details": {"provider": "instawp", "site_id": 555, "site_url": SITE + "/"}}]
        self.saved = {n: getattr(su, n) for n in ("_connection", "_wp_get", "_http_get", "_directory_fetch",
                                                 "_host_api", "_provisioned_rows", "_php_reported",
                                                 "_log_activity", "_alert", "_sleep")}
        su._connection = lambda cid: (SITE, "uallak", "app-pass")
        su._wp_get = self.site.wp_get
        su._http_get = self.site.http_get
        su._directory_fetch = lambda kind, slug: dict(self.site.directory.get((kind, slug)) or {})
        su._host_api = lambda: self.site
        su._provisioned_rows = lambda cid: self.provisioned
        su._php_reported = lambda cid, url: "8.1"
        su._log_activity = lambda cid, details: self.logs.append(details)
        su._alert = self.alerts.append
        su._sleep = lambda s: None
        su._status_cache.clear()
        su._directory_cache.clear()

    def tearDown(self):
        for name, fn in self.saved.items():
            setattr(su, name, fn)

    def offered(self):
        return su.plan(27, ["all"])["items"]

    def commands(self):
        return [c for c in self.site.calls if c[0] == "backup" or (c[0] == "cmd" and " update " in c[1])
                or c[0] == "restore"]


class StatusView(Base):
    def test_versions_active_state_and_flags(self):
        s = su.status(27)
        by = {f"{i['type']}:{i['slug']}": i for i in s["items"]}
        self.assertEqual((s["wordpress_version"], s["php_version"], s["versions_source"]), ("6.6.1", "8.1.2", "host"))
        self.assertTrue(s["we_can_update"])
        seo = by["plugin:wordpress-seo"]
        self.assertEqual((seo["installed"], seo["available"], seo["active"], seo["update"]), ("22.0", "22.1", True, True))
        self.assertEqual(seo["flags"], [])
        woo = by["plugin:woocommerce"]
        self.assertIn("php_incompatible", woo["flags"])            # needs PHP 8.3, the site runs 8.1.2
        self.assertNotIn("wp_incompatible", woo["flags"])
        self.assertIn("not_in_public_directory", by["plugin:premium-x"]["flags"])
        self.assertFalse(by["plugin:premium-x"]["active"])
        self.assertEqual(s["updates_available"], ["plugin:wordpress-seo", "theme:astra"])
        self.assertEqual(s["blocked_updates"], [{"item": "plugin:woocommerce", "name": "WooCommerce",
                                                 "flags": ["php_incompatible"]}])
        self.assertIn("never automatic", s["core_updates"])

    def test_wordpress_incompatibility_and_unknown(self):
        self.site.directory[("theme", "astra")]["requires_wp"] = "6.7"
        by = {i["slug"]: i for i in su.status(27)["items"]}
        self.assertIn("wp_incompatible", by["astra"]["flags"])
        self.assertFalse(su.offerable(by["astra"]))
        self.assertTrue(su._needs("8.3", "") is None)
        entry = su._entry("plugin", {"plugin": "wordpress-seo/wp-seo", "version": "22.0"}, {"php": "", "wp": ""})
        self.assertIn("compat_unknown", entry["flags"])
        self.assertTrue(su.offerable(entry))                      # unknown is flagged, not blocked

    def test_offer_line_is_plain_hebrew(self):
        self.assertEqual(su.status(27)["offer_line_he"],
                         "יש עדכון אחד לתוסף ועדכון אחד לתבנית, רוצה שאעדכן? אני אגבה לפני ואבדוק אחרי.")
        three = [{"type": "plugin"}] * 3
        self.assertEqual(su.offer_line(three), "יש 3 עדכונים לתוספים, רוצה שאעדכן? אני אגבה לפני ואבדוק אחרי.")
        self.assertNotIn("!", su.offer_line(three))

    def test_site_we_do_not_host(self):
        self.provisioned = [{"details": {"provider": "instawp", "site_id": 555, "site_url": "https://old.example"}}]
        s = su.status(27, refresh=True)
        self.assertFalse(s["we_can_update"])
        self.assertEqual(s["versions_source"], "public")
        self.assertEqual(s["wordpress_version"], "6.6.1")          # the generator tag
        self.assertEqual(s["offer_line_he"], "")
        self.assertEqual(su.plan(27, ["all"])["refuse"], "not_hosted")
        self.assertIn("team member runs them by hand", su.for_agent(27)["note"])

    def test_plan_only_offers_what_is_behind_and_compatible(self):
        self.assertEqual([i["slug"] for i in self.offered()], ["wordpress-seo", "astra"])
        self.assertEqual(su.plan(27, ["plugin:woocommerce"])["refuse"], "incompatible")
        self.site.directory[("plugin", "wordpress-seo")]["version"] = "22.0"
        self.site.directory[("theme", "astra")]["version"] = "4.0.0"
        su._directory_cache.clear()
        self.assertEqual(su.plan(27, ["all"])["refuse"], "incompatible")       # only the blocked one is left
        self.site.directory[("plugin", "woocommerce")]["version"] = "9.0.0"
        su._directory_cache.clear()
        self.assertEqual(su.plan(27, ["all"])["refuse"], "nothing_to_update")


class Flow(Base):
    def test_one_at_a_time_backup_before_each_and_logged(self):
        out = su.run_updates(27, self.offered(), who="client", proposal_id=71)
        self.assertEqual(out["stopped"], "")
        self.assertEqual([(u["slug"], u["from"], u["to"]) for u in out["updated"]],
                         [("wordpress-seo", "22.0", "22.1"), ("astra", "4.0.0", "4.1.0")])
        self.assertEqual(self.commands(), [("backup",), ("cmd", "wp plugin update wordpress-seo --no-color"),
                                           ("backup",), ("cmd", "wp theme update astra --no-color")])
        self.assertEqual([(l["who"], l["proposal_id"], l["slug"], l["from"], l["to"], l["backup_ref"], l["result"])
                          for l in self.logs],
                         [("client", 71, "wordpress-seo", "22.0", "22.1", "900", "updated"),
                          ("client", 71, "astra", "4.0.0", "4.1.0", "901", "updated")])
        self.assertEqual(self.alerts, [])

    def test_health_check_covers_key_pages_and_checkout(self):
        su.run_updates(27, self.offered()[:1], who="client")
        pages = set(self.site.fetched)
        self.assertEqual(pages, {SITE + "/", SITE + "/about/", SITE + "/checkout/"})   # never another host
        self.assertIn(("cmd", PROBE_OK), self.site.calls)                               # WordPress loads

    def test_already_current_item_is_skipped_without_backup(self):
        items = self.offered()
        self.site.plugins["wordpress-seo/wp-seo"]["version"] = "22.1"   # updated meanwhile
        out = su.run_updates(27, items[:1], who="client")
        self.assertEqual(out["skipped"][0]["result"], "already_current")
        self.assertEqual(self.commands(), [])

    def test_broken_site_is_never_touched(self):
        items = self.offered()
        self.site.broken = True
        out = su.run_updates(27, items, who="client")
        self.assertEqual(out["stopped"], "site_unhealthy")
        self.assertEqual(self.commands(), [])
        self.assertEqual(len(self.alerts), 1)


class BackupGating(Base):
    def test_no_backup_means_no_update(self):
        items = self.offered()
        self.site.backup_fails = True
        out = su.run_updates(27, items, who="client")
        self.assertEqual(out["stopped"], "backup_failed")
        self.assertEqual(out["updated"], [])
        self.assertEqual([i["slug"] for i in out["remaining"]], ["wordpress-seo", "astra"])
        self.assertFalse(any(c[0] == "cmd" and " update " in c[1] for c in self.site.calls))
        self.assertEqual(self.site.plugins["wordpress-seo/wp-seo"]["version"], "22.0")
        self.assertEqual(self.logs[0]["result"], "backup_failed")
        self.assertIn("could not be taken", self.alerts[0])

    def test_an_unverified_backup_counts_as_none(self):
        items = self.offered()
        self.site.backup_unlisted = True              # task said done, but the copy is not there
        out = su.run_updates(27, items, who="client")
        self.assertEqual(out["stopped"], "backup_failed")
        self.assertFalse(any(" update " in c[1] for c in self.site.calls if c[0] == "cmd"))

    def test_without_backup_only_on_its_own_card(self):
        out = su.run_updates(27, self.offered(), who="client", without_backup=True)
        self.assertEqual(len(out["updated"]), 2)
        self.assertNotIn(("backup",), self.site.calls)
        self.assertEqual([l["with_backup"] for l in self.logs], [False, False])


class Rollback(Base):
    def test_failed_health_check_restores_that_items_backup(self):
        self.site.break_on = {"astra"}
        out = su.run_updates(27, self.offered(), who="client", proposal_id=5)
        self.assertEqual(out["stopped"], "rolled_back")
        self.assertEqual([u["slug"] for u in out["updated"]], ["wordpress-seo"])
        self.assertEqual(self.commands()[-1], ("restore", "901"))   # astra's own backup, not the first one
        self.assertEqual(self.site.themes["astra"]["version"], "4.0.0")
        self.assertEqual(self.site.plugins["wordpress-seo/wp-seo"]["version"], "22.1")  # the good one stays
        self.assertFalse(self.site.broken)
        self.assertEqual(self.logs[-1]["result"], "rolled_back")
        self.assertEqual(self.logs[-1]["backup_ref"], "901")
        self.assertIn("ROLLED BACK", self.alerts[0])

    def test_failed_rollback_is_urgent(self):
        self.site.break_on = {"wordpress-seo"}
        self.site.unfixable = True
        out = su.run_updates(27, self.offered(), who="client")
        self.assertEqual(out["stopped"], "rollback_failed")
        self.assertEqual([i["slug"] for i in out["remaining"]], ["astra"])      # never continued
        self.assertTrue(self.alerts[0].startswith("client 27: site updates") and "URGENT" in self.alerts[0])

    def test_failure_without_backup_alerts_without_a_restore(self):
        self.site.break_on = {"wordpress-seo"}
        out = su.run_updates(27, self.offered(), who="client", without_backup=True)
        self.assertEqual(out["stopped"], "failed_no_backup")
        self.assertFalse(any(c[0] == "restore" for c in self.site.calls))
        self.assertIn("URGENT", self.alerts[0])


class NeverCore(unittest.TestCase):
    def test_only_plugin_and_theme_items(self):
        for bad in (["core:wordpress"], ["plugin:x; rm -rf /"], ["theme:$(id)"], [], ["plugin:a"] * 11):
            with self.assertRaises(ValueError, msg=bad):
                su.parse_request(bad)
        self.assertEqual(su.parse_request("all"), ["all"])
        self.assertEqual(su.parse_request(["plugin:wordpress-seo", "theme:astra"]),
                         ["plugin:wordpress-seo", "theme:astra"])
        with self.assertRaises(ValueError):
            su.validate_items([{"type": "core", "slug": "wordpress"}])
        with self.assertRaises(ValueError):
            su.update_command({"type": "plugin", "slug": "x && wp core update"})
        self.assertEqual(su.update_command({"type": "theme", "slug": "astra"}), "wp theme update astra --no-color")

    def test_no_core_update_anywhere(self):
        with open(os.path.join(ROOT, "core", "site_updates.py"), encoding="utf-8") as f:
            src = f.read().lower()
        self.assertNotIn("wp core update", src.replace("x && wp core update", ""))
        self.assertEqual(su.ITEM_TYPES, ("plugin", "theme"))


# ─── Through the cards (core/agent_actions) ──────────────────────────────────

class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.op, self.payload, self.filters = db, table, "select", None, []

    def select(self, *_a):
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, changes):
        self.op, self.payload = "update", changes
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self

    def in_(self, col, vals):
        vals = tuple(vals)
        self.filters.append(lambda r: r.get(col) in vals)
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        return self

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        if self.op == "insert":
            row = {"id": len(rows) + 1, "created_at": datetime.now(timezone.utc).isoformat(), **self.payload}
            rows.append(row)
            return types.SimpleNamespace(data=[dict(row)])
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
        return types.SimpleNamespace(data=[dict(r) for r in hit])


class Cards(unittest.TestCase):
    ITEMS = [{"type": "plugin", "slug": "wordpress-seo", "name": "Yoast SEO", "from": "22.0", "to": "22.1"},
             {"type": "theme", "slug": "astra", "name": "Astra", "from": "4.0.0", "to": "4.1.0"}]

    @classmethod
    def setUpClass(cls):
        from core import agent_actions
        cls.aa = agent_actions

    def setUp(self):
        aa = self.aa
        self.tables, self.posted, self.runs = {}, [], []
        self.saved = {n: getattr(aa, n) for n in ("_db", "_client", "_post_client_message", "_alert",
                                                 "_forget_state", "_log")}
        self.saved_su = {n: getattr(su, n) for n in ("plan", "run_updates")}
        aa._db = lambda: types.SimpleNamespace(table=lambda name: _Q(self.tables, name))
        aa._client = lambda cid: {"id": cid, "language": "he"}
        aa._post_client_message = lambda cid, persona, text: self.posted.append(text)
        aa._alert = lambda issue: None
        aa._forget_state = lambda cid: None
        aa._log = lambda *a, **k: None
        su.plan = lambda cid, requested: {"items": list(self.ITEMS), "blocked": []}
        self.outcome = {"updated": [], "skipped": [], "stopped": "", "remaining": [], "problems": []}

        def run_updates(cid, items, who, proposal_id=None, without_backup=False):
            self.runs.append((who, proposal_id, without_backup, [i["slug"] for i in items]))
            return dict(self.outcome)
        su.run_updates = run_updates

    def tearDown(self):
        for n, fn in self.saved.items():
            setattr(self.aa, n, fn)
        for n, fn in self.saved_su.items():
            setattr(su, n, fn)

    def propose(self):
        out = self.aa.propose(27, "client_chat", "website", {"name": "update_site_items", "params": {"items": "all"}})
        self.assertIn("card", out, out)
        return out["card"]

    def approve(self, card):
        return self.aa.decide(card["id"], "client", 27, "approve", background_tasks=None)

    def test_card_names_each_item_and_the_backup(self):
        card = self.propose()
        self.assertEqual(card["title"], "עדכון תוספים ותבניות באתר")
        self.assertIn("• Yoast SEO: 22.0 → 22.1", card["what"])
        self.assertIn("לפני כל עדכון נגבה את כל האתר", card["what"])
        self.assertTrue(card["can_decide"])
        self.assertNotIn("InstaWP", card["what"])

    def test_done_lists_versions_and_who_approved_is_passed(self):
        card = self.propose()
        self.outcome["updated"] = [dict(i) for i in self.ITEMS]
        out = self.approve(card)
        self.assertEqual(out["status"], "done")
        self.assertEqual(self.runs, [("client", card["id"], False, ["wordpress-seo", "astra"])])
        self.assertIn("• Astra: 4.0.0 → 4.1.0", self.posted[-1])

    def test_backup_failure_opens_the_no_backup_card_with_the_rest(self):
        card = self.propose()
        self.outcome.update(stopped="backup_failed", remaining=[self.ITEMS[1]], updated=[self.ITEMS[0]])
        out = self.approve(card)
        self.assertEqual((out["status"], out["code"]), ("failed", "backup_failed"))
        self.assertIn("לא הצלחתי לגבות את האתר", out["message"])
        nxt = out["next_card"]
        self.assertEqual(nxt["action"], "update_site_items_without_backup")
        self.assertEqual(nxt["title"], "עדכון בלי גיבוי — רק באישור שלך")
        self.assertIn("• Astra: 4.0.0 → 4.1.0", nxt["what"])
        self.assertNotIn("Yoast", nxt["what"])
        self.assertTrue(nxt["can_decide"])
        # and only an explicit click on it runs the update without a backup
        self.outcome.update(stopped="", remaining=[], updated=[self.ITEMS[1]])
        self.assertEqual(self.approve(nxt)["status"], "done")
        self.assertEqual(self.runs[-1][2:], (True, ["astra"]))

    def test_a_model_can_never_propose_the_no_backup_card(self):
        out = self.aa.propose(27, "client_chat", "website",
                              {"name": "update_site_items_without_backup", "params": {"items": self.ITEMS}})
        self.assertEqual(out, {"error": "not_allowed_here"})
        self.assertNotIn("update_site_items_without_backup",
                         [a["name"] for a in self.aa.available(27, "client_chat", {}, {"id": 27})])
        self.assertFalse(self.aa.staff_may_decide(self.aa.ACTIONS["update_site_items_without_backup"]))

    def test_rollback_is_told_plainly(self):
        card = self.propose()
        self.outcome.update(stopped="rolled_back", problems=["https://x/: HTTP 500"])
        out = self.approve(card)
        self.assertEqual(out["code"], "rolled_back")
        self.assertIn("החזרתי את האתר מיד מהגיבוי", out["message"])
        self.assertNotIn("500", out["message"])
        self.outcome.update(updated=[self.ITEMS[0]])
        out = self.approve(self.propose())
        self.assertEqual(out["code"], "rolled_back_partial")

    def test_offered_only_when_we_can_update(self):
        names = lambda state: [a["name"] for a in self.aa.available(27, "client_chat", state, {"id": 27})]
        self.assertIn("update_site_items", names({"site_updates": {"we_can_update": True,
                                                                    "updates": [{"item": "plugin:a"}]}}))
        self.assertNotIn("update_site_items", names({"site_updates": {"we_can_update": False,
                                                                       "updates": [{"item": "plugin:a"}]}}))
        self.assertNotIn("update_site_items", names({"site_updates": {"we_can_update": True, "updates": []}}))


if __name__ == "__main__":
    unittest.main()
