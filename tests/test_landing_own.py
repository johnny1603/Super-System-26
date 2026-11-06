"""uallak-hosted landing pages (docs/LANDING_PAGES.md): versions + publish +
one-tap revert, sanitization, tracking events, the form → leads path and its
spam checks, per-client isolation, unpublish, limits, the publish card, the
backup-first migration, and the static guards on routes and the dashboard.

Stdlib only: the database is an in-memory fake with the query shapes these
modules use; agents are stubbed in sys.modules.
"""
import os
import re
import sys
import types
import unittest
from datetime import date, datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SESSION_SECRET_KEY", "test-only-not-a-secret")

from core import landing_pages as lp        # noqa: E402
from core import landing_tracking as lt     # noqa: E402
from core import landing_admin as la        # noqa: E402


def _src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


# ─── In-memory Supabase stand-in ─────────────────────────────────────────────

class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.op = db, table, "select"
        self.filters, self.payload, self.limit_n, self.count = [], None, None, None
        self.order_col, self.desc = None, False

    def select(self, *_a, count=None):
        self.op, self.count = "select", count
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def update(self, changes):
        self.op, self.payload = "update", changes
        return self

    def upsert(self, row, on_conflict=""):
        self.op, self.payload, self.key = "upsert", row, on_conflict
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def gte(self, col, val):
        self.filters.append(lambda r, c=col, v=val: str(r.get(c)) >= str(v))
        return self

    def lt(self, col, val):
        self.filters.append(lambda r, c=col, v=val: str(r.get(c)) < str(v))
        return self

    def in_(self, col, vals):
        self.filters.append(lambda r, c=col, v=tuple(vals): r.get(c) in v)
        return self

    def order(self, col, desc=False):
        self.order_col, self.desc = col, desc
        return self

    def limit(self, n):
        self.limit_n = n
        return self

    def execute(self):
        if self.table in self.db.missing:
            raise RuntimeError(f"PGRST205 Could not find the table public.{self.table}")
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "insert":
            row = {"id": self.db.next_id(), "created_at": datetime.now(timezone.utc).isoformat(), **self.payload}
            for col in self.db.missing_cols.get(self.table, ()):
                if col in row:
                    raise RuntimeError(f"column {col} does not exist")
            rows.append(row)
            return types.SimpleNamespace(data=[dict(row)], count=None)
        if self.op == "upsert":
            key = self.key or "id"
            for r in rows:
                if r.get(key) == self.payload.get(key):
                    r.update(self.payload)
                    return types.SimpleNamespace(data=[dict(r)], count=None)
            rows.append(dict(self.payload))
            return types.SimpleNamespace(data=[dict(self.payload)], count=None)
        matched = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for col in self.db.missing_cols.get(self.table, ()):
                if col in self.payload:
                    raise RuntimeError(f"column {col} does not exist")
            for r in matched:
                r.update(self.payload)
            return types.SimpleNamespace(data=[dict(r) for r in matched], count=None)
        if self.op == "delete":
            for r in matched:
                rows.remove(r)
            return types.SimpleNamespace(data=[dict(r) for r in matched], count=None)
        if self.order_col:
            matched = sorted(matched, key=lambda r: r.get(self.order_col) or 0, reverse=self.desc)
        return types.SimpleNamespace(data=[dict(r) for r in matched[:self.limit_n or None]],
                                     count=len(matched) if self.count else None)


class _Rpc:
    def __init__(self, db, name, params):
        self.db, self.name, self.params = db, name, params

    def execute(self):
        if "landing_page_stats" in self.db.missing:
            raise RuntimeError("PGRST202 Could not find the function public.landing_stat_bump")
        p = self.params
        assert p["p_field"] in lt.FIELDS
        rows = self.db.tables.setdefault("landing_page_stats", [])
        today = date.today().isoformat()
        for r in rows:
            if r["page_id"] == p["p_page"] and r["day"] == today:
                break
        else:
            r = {"page_id": p["p_page"], "client_id": p["p_client"], "day": today,
                 "views": 0, "calls": 0, "whatsapp": 0, "store": 0, "bytes": 0}
            rows.append(r)
        r[p["p_field"]] += 1
        r["bytes"] += p["p_bytes"]
        return types.SimpleNamespace(data=None)


class FakeDB:
    def __init__(self):
        self.tables, self.missing, self.missing_cols, self._id = {}, set(), {}, 100

    def next_id(self):
        self._id += 1
        return self._id

    def table(self, name):
        return _Q(self, name)

    def rpc(self, name, params):
        return _Rpc(self, name, params)


class Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.saved_db = lp._db
        lp._db = lambda: self.db
        lp._versions_ok["value"] = True
        lt._stats_ok["value"] = True
        lt._ip_hits.clear()
        lt._gtm_cache.clear()

    def tearDown(self):
        lp._db = self.saved_db
        lp._versions_ok["value"] = True
        lt._stats_ok["value"] = True

    def page(self, client_id=7, title="Offer", content=None, contact=None):
        created = lp.create_page(client_id, title, "goal")
        self.assertTrue(created["success"], created)
        page_id = created["page"]["id"]
        lp.update_page(client_id, page_id, {"content": content or {"headline": f"H {title}", "cta_text": "שלחו"},
                                            "contact": contact or {}}, source="generated")
        return page_id


# ─── Sanitization ────────────────────────────────────────────────────────────

class Sanitization(Base):
    def test_tags_script_urls_and_bidi_are_stripped_on_the_way_in(self):
        content = lp.normalize_content({
            "headline": '<script>alert(1)</script>מבצע <b>חם</b>',
            "subheadline": "click javascript:alert(1)",
            "benefits": ["<img src=x onerror=alert(1)>יתרון", "‮טקסט הפוך"],
            "sections": [{"title": "<a href='x'>t</a>", "body": "data:text/html,<b>x</b>"}],
            "cta_text": "<iframe>שלחו"})
        flat = repr(content)
        for bad in ("<script", "<img", "<iframe", "<a ", "javascript:", "data:", "‮", "onerror=alert(1)>"):
            self.assertNotIn(bad, flat)
        self.assertIn("מבצע", content["headline"])

    def test_text_comparisons_survive(self):
        self.assertEqual(lp.sanitize_text("מחיר < 100 ש\"ח"), "מחיר < 100 ש\"ח")

    def test_render_escapes_whatever_reaches_it(self):
        page = {"id": 1, "slug": "x", "title": '"><script>x</script>',
                "content": {"headline": "&lt;script&gt;alert(1)&lt;/script&gt;"}}
        html_text = lp.render_page(page, "https://a/api/leads/capture/t", "https://a/lp/c7/x")
        self.assertNotIn("<script>x", html_text)
        self.assertNotIn("<script>alert", html_text)
        self.assertNotIn("<script", html_text)   # no tag manager → no script at all

    def test_contact_links_are_validated(self):
        self.assertEqual(lp.normalize_contact({"phone": "050-123-4567", "whatsapp": "+972 50 1234567",
                                               "store_url": "https://shop.example.co.il/p"}),
                         {"phone": "0501234567", "whatsapp": "972501234567",
                          "store_url": "https://shop.example.co.il/p"})
        for bad in ("javascript:alert(1)", "http://shop.example.com", "https://user@evil.com",
                    "https://no spaces.com", "https://localhost"):
            self.assertNotIn("store_url", lp.normalize_contact({"store_url": bad}), bad)
        self.assertEqual(lp.normalize_contact({"phone": "12"}), {})

    def test_legal_pages_escape_the_business_name(self):
        text = lp.render_legal("privacy", "<b>פיצה</b>")
        self.assertNotIn("<b>פיצה", text)
        self.assertIn('dir="rtl"', text)
        self.assertIn("מדיניות פרטיות", text)

    def test_reserved_slugs(self):
        page_id = lp.create_page(7, "privacy")["page"]["slug"]
        self.assertNotEqual(page_id, "privacy")


# ─── Versions, publish, revert, unpublish ────────────────────────────────────

class Versions(Base):
    def test_edit_live_page_does_not_change_live_until_published(self):
        pid = self.page(content={"headline": "גרסה ראשונה", "cta_text": "שלחו"})
        self.assertEqual(lp.publish(7, pid)["version"], 1)
        lp.update_page(7, pid, {"content": {"headline": "גרסה שנייה", "cta_text": "שלחו"}})
        page = lp.get_page(7, pid)
        self.assertEqual(page["current_version"], 2)
        self.assertEqual(lp.visible_of(7, page)["content"]["headline"], "גרסה ראשונה")
        self.assertEqual(lp.publish(7, pid)["version"], 2)
        self.assertEqual(lp.visible_of(7, lp.get_page(7, pid))["content"]["headline"], "גרסה שנייה")

    def test_revert_is_a_new_version_and_goes_live_on_a_live_page(self):
        pid = self.page(content={"headline": "A", "cta_text": "x"})
        lp.publish(7, pid)
        lp.update_page(7, pid, {"content": {"headline": "B", "cta_text": "x"}})
        lp.publish(7, pid)
        result = lp.revert(7, pid, 1)
        self.assertTrue(result["success"])
        page = lp.get_page(7, pid)
        self.assertEqual(page["current_version"], 3)
        self.assertEqual(page["published_version"], 3)
        self.assertEqual(lp.visible_of(7, page)["content"]["headline"], "A")
        self.assertEqual([v["version"] for v in lp.list_versions(7, pid)], [3, 2, 1])
        self.assertEqual(lp.get_version(7, pid, 3)["source"], "revert:1")

    def test_revert_on_a_draft_does_not_publish(self):
        pid = self.page(content={"headline": "A", "cta_text": "x"})
        lp.update_page(7, pid, {"content": {"headline": "B", "cta_text": "x"}})
        lp.revert(7, pid, 1)
        self.assertEqual(lp.get_page(7, pid)["status"], "draft")

    def test_unpublish_is_instant(self):
        pid = self.page()
        lp.publish(7, pid)
        self.assertTrue(lp.unpublish(7, pid, "admin")["success"])
        page = lp.get_page(7, pid)
        self.assertEqual(page["status"], "draft")
        self.assertEqual(page["unpublished_reason"], "admin")

    def test_status_is_not_writable_through_update(self):
        pid = self.page()
        lp.update_page(7, pid, {"title": "t", "status": "published", "client_id": 9})
        page = lp.get_page(7, pid)
        self.assertEqual(page["status"], "draft")
        self.assertEqual(page["client_id"], 7)

    def test_active_limit_is_server_side(self):
        ids = [self.page(title=f"p{i}") for i in range(3)]
        for pid in ids:
            self.assertTrue(lp.publish(7, pid)["success"])
        lp.unpublish(7, ids[0])
        self.assertTrue(lp.publish(7, ids[0])["success"])
        # a 4th page can't even be created (ceiling), and a 4th LIVE one can't go up
        self.assertEqual(lp.create_page(7, "p4")["code"], "ERR_LANDING_PAGE_LIMIT")

    def test_active_limit_refuses_one_more_live(self):
        saved = lp.MAX_ACTIVE_PAGES
        lp.MAX_ACTIVE_PAGES = 1
        try:
            a, b = self.page(title="a"), self.page(title="b")
            self.assertTrue(lp.publish(7, a)["success"])
            self.assertEqual(lp.publish(7, b)["code"], "ERR_LANDING_ACTIVE_LIMIT")
            self.assertTrue(lp.publish(7, a)["success"])   # re-publishing a live page is fine
        finally:
            lp.MAX_ACTIVE_PAGES = saved

    def test_size_limit(self):
        pid = self.page()
        saved = lp.MAX_PAGE_BYTES
        lp.MAX_PAGE_BYTES = 500
        try:
            self.assertEqual(lp.update_page(7, pid, {"title": "longer"})["code"], "ERR_LANDING_TOO_LARGE")
            self.assertEqual(lp.publish(7, pid)["code"], "ERR_LANDING_TOO_LARGE")
        finally:
            lp.MAX_PAGE_BYTES = saved

    def test_before_the_migration_pages_still_work(self):
        self.db.missing.add("landing_page_versions")
        self.db.missing_cols["landing_pages"] = ("current_version", "contact", "published_version",
                                                 "published_at", "unpublished_reason")
        created = lp.create_page(7, "old", "g")
        pid = created["page"]["id"]
        self.assertTrue(lp.update_page(7, pid, {"content": {"headline": "H", "cta_text": "x"}})["success"])
        self.assertTrue(lp.publish(7, pid)["success"])
        page = lp.get_page(7, pid)
        self.assertEqual(page["status"], "published")
        self.assertEqual(lp.visible_of(7, page)["content"]["headline"], "H")
        self.assertTrue(lp.unpublish(7, pid)["success"])


# ─── Per-client isolation ────────────────────────────────────────────────────

class Isolation(Base):
    def test_another_clients_page_and_versions_never_resolve(self):
        pid = self.page(client_id=7)
        self.assertEqual(lp.get_page(8, pid), {})
        self.assertEqual(lp.get_version(8, pid, 1), {})
        self.assertEqual(lp.list_versions(8, pid), [])
        self.assertEqual(lp.publish(8, pid)["code"], "ERR_LANDING_NOT_FOUND")
        self.assertEqual(lp.unpublish(8, pid)["code"], "ERR_LANDING_NOT_FOUND")
        self.assertEqual(lp.revert(8, pid, 1)["code"], "ERR_LANDING_NO_VERSION")
        self.assertEqual(lp.get_page(7, pid)["status"], "draft")

    def test_stats_are_scoped_by_client(self):
        a, b = self.page(client_id=7), self.page(client_id=8)
        lt.bump(a, 7, "views", 100)
        lt.bump(b, 8, "views", 100)
        lt.bump(b, 8, "calls")
        self.assertEqual(set(lt.stats_by_page(7)), {a})
        self.assertEqual(lt.stats_by_page(8)[b]["calls"], 1)

    def test_form_stamp_is_bound_to_the_client(self):
        stamp = lt.make_stamp(7, now=1000)
        self.assertIsNone(lt._stamp_time(stamp, 8))
        self.assertEqual(lt._stamp_time(stamp, 7), 1000)


# ─── Tracking ────────────────────────────────────────────────────────────────

class Tracking(Base):
    def render(self, **kw):
        page = {"id": 1, "slug": "spring", "title": "t", "content": {"headline": "h", "cta_text": "c"}}
        links = {"call": "spring/go/call", "whatsapp": "spring/go/whatsapp", "privacy": "privacy", "terms": "terms"}
        return lp.render_page(page, "https://app/api/leads/capture/T", "https://app/lp/c7/spring",
                              links=links, form_stamp="S", **kw)

    def test_gtm_and_events_only_when_a_container_is_given(self):
        plain = self.render()
        self.assertNotIn("<script", plain)
        tagged = self.render(gtm_container="GTM-ABC123")
        self.assertIn("googletagmanager.com/gtm.js", tagged)
        self.assertIn("'GTM-ABC123'", tagged)
        for event in ("page_view", "generate_lead", "click_to_call", "click_whatsapp", "begin_checkout"):
            self.assertIn(event, tagged)
        self.assertNotIn("googletagmanager", self.render(gtm_container="GTM-1');alert(1);//"))

    def test_preview_never_carries_tag_manager_and_cannot_submit(self):
        text = self.render(gtm_container="GTM-ABC123", preview=True)
        self.assertNotIn("<script", text)
        self.assertIn("noindex", text)
        self.assertIn("disabled", text)

    def test_buttons_carry_data_track(self):
        text = self.render()
        self.assertIn('href="spring/go/call" data-track="call"', text)
        self.assertIn('data-track="whatsapp"', text)

    def test_gtm_only_on_the_clients_verified_hostname(self):
        domains = types.ModuleType("core.landing_domains")
        domains.get_state = lambda cid: {"mode": "active", "hostname": "lp.pizza.co.il"}
        gtm = types.ModuleType("core.gtm_connection")
        gtm._connection = lambda cid: {"access_token": "t", "account_id": "accounts/1/containers/2"}
        gtm._recorded_container = lambda cid, path: {"public_id": "GTM-PIZZA1"}
        saved = {k: sys.modules.get(k) for k in ("core.landing_domains", "core.gtm_connection")}
        sys.modules["core.landing_domains"], sys.modules["core.gtm_connection"] = domains, gtm
        import core
        saved_attrs = {k: getattr(core, k, None) for k in ("landing_domains", "gtm_connection")}
        core.landing_domains, core.gtm_connection = domains, gtm
        try:
            self.assertEqual(lt.gtm_container(7, ""), "")                    # app.uallak.com: never
            self.assertEqual(lt.gtm_container(7, "evil.example.com"), "")    # someone else's host
            self.assertEqual(lt.gtm_container(7, "LP.pizza.co.il"), "GTM-PIZZA1")
            lt._gtm_cache.clear()
            domains.get_state = lambda cid: {"mode": "pending", "hostname": "lp.pizza.co.il"}
            self.assertEqual(lt.gtm_container(7, "lp.pizza.co.il"), "")      # not verified yet
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
            for k, v in saved_attrs.items():
                if v is None:
                    delattr(core, k)
                else:
                    setattr(core, k, v)

    def test_counters_are_atomic_rpc_and_off_before_the_migration(self):
        pid = self.page()
        lt.bump(pid, 7, "views", 2000)
        lt.bump(pid, 7, "views", 2000)
        lt.bump(pid, 7, "whatsapp")
        lt.bump(pid, 7, "nonsense")
        totals = lt.stats_by_page(7)[pid]
        self.assertEqual((totals["views"], totals["whatsapp"], totals["bytes"]), (2, 1, 4000))
        self.db.missing.add("landing_page_stats")
        lt.bump(pid, 7, "views")          # silent
        self.assertFalse(lt._stats_ok["value"])
        self.assertEqual(lt.stats_by_page(7), {})


# ─── The form → leads, and spam ──────────────────────────────────────────────

class FormToLeads(Base):
    def test_form_posts_to_the_capture_endpoint_with_page_attribution(self):
        page = {"id": 1, "slug": "spring", "title": "t", "content": {"headline": "h", "cta_text": "c"}}
        text = lp.render_page(page, "https://app/api/leads/capture/TOKEN", "https://app/lp/c7/spring",
                              links={"privacy": "privacy", "terms": "terms"}, form_stamp="STAMP",
                              business_name="פיצה")
        self.assertIn('action="https://app/api/leads/capture/TOKEN" method="post"', text)
        self.assertIn('name="source_detail" value="lp:spring"', text)
        self.assertIn('name="lp_stamp" value="STAMP"', text)
        self.assertIn('name="website"', text)                       # the honeypot
        self.assertIn('class="consent"', text)
        self.assertIn('href="privacy"', text)
        self.assertIn('href="terms"', text)
        self.assertIn('value="https://app/lp/c7/spring?sent=1"', text)
        self.assertIn('dir="rtl"', text)

    def test_spam_rules(self):
        good = {"name": "דנה", "source_detail": "lp:spring", "lp_stamp": lt.make_stamp(7, now=1000)}
        self.assertTrue(lt.is_landing_submit(good))
        self.assertFalse(lt.is_landing_submit({"name": "x", "source_detail": "https://site"}))
        self.assertEqual(lt.spam_reason(7, good, "1.1.1.1", now=1010), "")
        self.assertEqual(lt.spam_reason(7, {**good, "website": "http://x"}, "1.1.1.1", now=1010), "honeypot")
        self.assertEqual(lt.spam_reason(7, good, "1.1.1.2", now=1001), "too_fast")
        self.assertEqual(lt.spam_reason(7, good, "1.1.1.2", now=1000 + lt.STAMP_MAX_AGE + 1), "stale")
        self.assertEqual(lt.spam_reason(7, {**good, "lp_stamp": "x.y"}, "1.1.1.2", now=1010), "stamp")
        self.assertEqual(lt.spam_reason(8, good, "1.1.1.2", now=1010), "stamp")      # another client's stamp

    def test_per_ip_limit(self):
        good = {"source_detail": "lp:spring", "lp_stamp": lt.make_stamp(7, now=1000)}
        reasons = [lt.spam_reason(7, good, "9.9.9.9", now=1010 + i) for i in range(lt.MAX_SUBMITS_PER_IP + 1)]
        self.assertEqual(reasons[:-1], [""] * lt.MAX_SUBMITS_PER_IP)
        self.assertEqual(reasons[-1], "rate")
        self.assertEqual(lt.spam_reason(7, good, "8.8.8.8", now=1020), "")              # another visitor
        self.assertEqual(lt.spam_reason(7, good, "9.9.9.9", now=1010 + lt.WINDOW_SECONDS + 10), "")

    def test_our_fields_never_reach_the_lead_row(self):
        self.assertEqual(lt.clean_payload({"name": "a", "website": "", "lp_stamp": "s"}), {"name": "a"})

    def test_capture_endpoint_checks_spam_before_storing(self):
        src = _src("core/api_server.py")
        body = src[src.index('async def public_lead_capture'):src.index('# ─── External CRM')]
        self.assertLess(body.index("landing_tracking.spam_reason"), body.index("capture_lead("))
        self.assertIn("clean_payload", body)


# ─── The publish card ────────────────────────────────────────────────────────

class PublishCard(Base):
    def setUp(self):
        super().setUp()
        from core import agent_actions as aa
        self.aa = aa
        self.saved = {n: getattr(aa, n) for n in ("_db", "_client", "_post_client_message", "_alert", "_log")}
        aa._db = lambda: self.db
        aa._client = lambda cid: {"id": cid, "language": "he"}
        self.posted = []
        aa._post_client_message = lambda cid, persona, text: self.posted.append((cid, persona, text))
        aa._alert = lambda issue: None
        aa._log = lambda *a, **k: None
        self.published = []
        agent = types.ModuleType("agents.landing_page_agent")

        def publish_page(client_id, page_id, version=None, notify=True):
            self.published.append((client_id, page_id, version, notify))
            result = lp.publish(client_id, page_id, version)
            if not result.get("success"):
                return result
            return {**result, "url": "https://app.uallak.com/lp/c7/offer", "shared_url": True,
                    "tracking_offered": False}
        agent.publish_page = publish_page
        self.saved_agent = sys.modules.get("agents.landing_page_agent")
        sys.modules["agents.landing_page_agent"] = agent

    def tearDown(self):
        for n, fn in self.saved.items():
            setattr(self.aa, n, fn)
        if self.saved_agent is None:
            sys.modules.pop("agents.landing_page_agent", None)
        else:
            sys.modules["agents.landing_page_agent"] = self.saved_agent
        super().tearDown()

    def propose(self, page_id, client_id=7):
        return self.aa.propose(client_id, "client_chat", "website",
                               {"name": "publish_landing_page", "params": {"page_id": page_id}})

    def test_card_pins_the_newest_version_with_a_preview(self):
        pid = self.page()
        lp.update_page(7, pid, {"content": {"headline": "v2", "cta_text": "x"}})
        card = self.propose(pid)["card"]
        self.assertEqual(card["details"]["preview_url"], f"/api/client/landing-pages/{pid}/preview?version=2")
        self.assertIn("Offer", card["what"])
        self.assertTrue(card["can_decide"])

    def test_nothing_is_live_until_the_click_and_the_click_publishes_that_version(self):
        pid = self.page()
        card = self.propose(pid)["card"]
        lp.update_page(7, pid, {"content": {"headline": "v2 after the card", "cta_text": "x"}})
        self.assertEqual(lp.get_page(7, pid)["status"], "draft")
        out = self.aa.decide(card["id"], "client", 7, "approve")
        self.assertEqual(out["status"], "done", out)
        self.assertEqual(self.published, [(7, pid, 1, False)])
        page = lp.get_page(7, pid)
        self.assertEqual((page["status"], page["published_version"]), ("published", 1))
        self.assertIn("https://app.uallak.com/lp/c7/offer", self.posted[-1][2])
        self.assertEqual(self.posted[-1][1], "website")

    def test_a_newer_card_replaces_the_older_one(self):
        pid = self.page()
        first = self.propose(pid)["card"]
        lp.update_page(7, pid, {"content": {"headline": "v2", "cta_text": "x"}})
        second = self.propose(pid)["card"]
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.aa.decide(first["id"], "client", 7, "approve").get("already"), True)
        self.assertEqual(self.propose(pid)["card"]["id"], second["id"])     # same version: same card

    def test_another_clients_page_cannot_be_proposed_or_approved(self):
        pid = self.page(client_id=8)
        self.assertEqual(self.propose(pid, client_id=7)["error"], "invalid_params")
        mine = self.page(client_id=7, title="mine")
        card = self.propose(mine)["card"]
        self.assertEqual(self.aa.decide(card["id"], "client", 8, "approve")["code"], "not_found")

    def test_limit_refusal_is_a_plain_line(self):
        saved = lp.MAX_ACTIVE_PAGES
        lp.MAX_ACTIVE_PAGES = 0
        try:
            card = self.propose(self.page())["card"]
            out = self.aa.decide(card["id"], "client", 7, "approve")
            self.assertEqual(out["code"], "active_limit")
            self.assertIn("כבר באוויר", out["message"])
        finally:
            lp.MAX_ACTIVE_PAGES = saved


# ─── Migration: backup first ─────────────────────────────────────────────────

class Migration(Base):
    def setUp(self):
        super().setUp()
        self.saved_audit = la._audit
        la._audit = lambda message: None

    def tearDown(self):
        la._audit = self.saved_audit
        super().tearDown()

    def old_page(self, client_id, status):
        row = {"id": self.db.next_id(), "client_id": client_id, "slug": f"old-{status}", "title": "ישן",
               "goal": "", "status": status, "copy_source": "",
               "content": {"headline": "דף שנבנה לפני הגרסאות", "cta_text": "שלחו"},
               "created_at": "2026-09-01", "updated_at": "2026-09-02"}
        self.db.tables.setdefault("landing_pages", []).append(row)
        return row["id"]

    def test_migration_needs_the_backup_and_keeps_what_is_live(self):
        live, draft = self.old_page(7, "published"), self.old_page(9, "draft")
        self.assertEqual(la.migrate("")["code"], "backup_required")
        backup = la.backup()
        self.assertEqual(backup["pages"], 2)
        self.assertIn("דף שנבנה לפני הגרסאות", backup["body"])
        self.assertEqual(la.migrate("0" * 64)["code"], "backup_required")
        result = la.migrate(backup["sha256"])
        self.assertEqual(sorted(result["migrated"]), sorted([live, draft]))
        page = lp.get_page(7, live)
        self.assertEqual((page["current_version"], page["published_version"]), (1, 1))
        self.assertEqual(lp.visible_of(7, page)["content"]["headline"], "דף שנבנה לפני הגרסאות")
        self.assertIsNone(lp.get_page(9, draft).get("published_version"))
        self.assertEqual(lp.get_version(7, live, 1)["source"], "migrated")
        again = la.migrate(backup["sha256"])
        self.assertEqual((again["migrated"], sorted(again["skipped"])), ([], sorted([live, draft])))
        self.assertEqual(len(self.db.tables["landing_pages"]), 2)          # nothing deleted

    def test_a_tampered_backup_does_not_verify(self):
        self.old_page(7, "published")
        backup = la.backup()
        stored = next(r for r in self.db.tables["app_settings"] if r["key"] == la.BACKUP_KEY)
        stored["value"]["body"] += " "
        self.assertEqual(la.migrate(backup["sha256"])["code"], "backup_required")

    def test_hosting_estimate_needs_rates_and_records_once(self):
        self.assertEqual(la.record_hosting("2026-10")["reason"], "rates_not_set")
        la.set_rates(0.5, 0.2)
        self.db.tables["landing_page_stats"] = [
            {"page_id": 5, "client_id": 7, "day": "2026-10-03", "views": 9000, "calls": 1000,
             "whatsapp": 0, "store": 0, "bytes": 2_000_000_000},
            {"page_id": 5, "client_id": 7, "day": "2026-11-01", "views": 99, "calls": 0,
             "whatsapp": 0, "store": 0, "bytes": 1}]
        recorded = []
        tracker = types.ModuleType("core.cost_tracker")
        import contextlib
        tracker.cost_action = lambda action, ref=None: contextlib.nullcontext()
        tracker.record_cost = lambda *a, **k: recorded.append((a, k))
        saved = sys.modules.get("core.cost_tracker")
        sys.modules["core.cost_tracker"] = tracker
        try:
            self.assertEqual(la.record_hosting("2026-10")["recorded"], 1)
            self.assertEqual(la.record_hosting("2026-10")["reason"], "already_recorded")
        finally:
            if saved is None:
                sys.modules.pop("core.cost_tracker", None)
            else:
                sys.modules["core.cost_tracker"] = saved
        (args, kwargs), = recorded
        self.assertEqual(args[0], "landing_hosting")
        self.assertAlmostEqual(args[1], 2 * 0.5 + 1 * 0.2)          # 2 GB + 10k requests
        self.assertEqual(args[2], 7)
        self.assertTrue(args[3]["estimate"])


# ─── Static guards: routes, dashboard, deletion ──────────────────────────────

class StaticGuards(unittest.TestCase):
    def test_route_order(self):
        src = _src("core/api_server.py")
        privacy = src.index('@app.get("/lp/{client_segment}/privacy"')
        go = src.index('@app.get("/lp/{client_segment}/{page_slug}/go/{kind}"')
        page = src.index('@app.get("/lp/{client_segment}/{page_slug}"')
        self.assertLess(privacy, page)
        self.assertLess(go, page)
        self.assertGreater(src.index('app.mount("/", StaticFiles'), page)
        self.assertTrue(src.rstrip().endswith('name="landing")'))

    def test_public_page_serves_the_live_version_and_tag_manager_only_via_the_worker_header(self):
        src = _src("core/api_server.py")
        body = src[src.index("def serve_landing_page"):src.index("# Must be last")]
        self.assertIn("visible_of(client_id, page)", body)
        self.assertIn("landing_tracking.LP_HOST_HEADER", body)

    def test_go_targets_come_only_from_the_stored_contact(self):
        src = _src("core/api_server.py")
        body = src[src.index("def landing_go"):src.index("def serve_landing_page")]
        body = body[:body.index("_LP_MEDIA_NAME")]
        self.assertNotIn("query_params", body)
        # the number / URL comes from the LIVE version's contact; the request
        # only supplies the WhatsApp text (tests/test_landing_creative.py)
        self.assertIn("live = landing_pages.visible_of(client_id, page)", body)
        self.assertIn('button_target(live["contact"], kind, text=text', body)

    def test_dashboard_publish_opens_the_card_never_publishes(self):
        html_text = _src("dashboard/client/index.html")
        fn = html_text[html_text.index("async function landingPublish"):]
        fn = fn[:fn.index("\n}\n")]
        self.assertIn("openAgentWindow", fn)
        self.assertIn("appendActionCard", fn)

    def test_deletion_covers_the_new_tables(self):
        from core import client_deletion
        names = [t for t, _ in client_deletion.DATA_TABLES]
        self.assertLess(names.index("landing_page_versions"), names.index("landing_pages"))
        self.assertIn("landing_page_stats", names)

    def test_migration_is_idempotent(self):
        sql = _src("migrations/2026-11-01-landing-own.sql")
        for line in re.findall(r"(?im)^(create table|create index|create unique index|alter table)[^\n]*", sql):
            pass
        self.assertNotRegex(sql, r"(?i)create table (?!if not exists)")
        self.assertNotRegex(sql, r"(?i)add column (?!if not exists)")
        self.assertNotRegex(sql, r"(?i)create (unique )?index (?!if not exists)")


if __name__ == "__main__":
    unittest.main()
