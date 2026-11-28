"""The client approves the brand brief, and "decide" means something (2026-11-28, governance §1).

- a draft is information, approval is authority: the agents read a draft, and
  nothing is blocked on approval;
- only the CLIENT's approval (or the owner's, on behalf of a client who was
  asked and stayed silent 24 h, recorded and said as such) grants authority;
  an approval made by the manager before this rule grants nothing;
- with the client's switch on, the brief granted and the weekly plan approved
  by the client, the plan's items are not approved a second time — and in
  every other case they wait as always.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_social_queue as tsq      # noqa: E402  (stubs + an in-memory app_settings)

from core import brand_brief as bb, brief_approval as ba, client_settings, client_speech as cs, content_team, governance  # noqa: E402
from core import social_queue  # noqa: E402

ROOT = os.path.dirname(HERE)
CID = 500
TEXT = "# Brand Brief — Test\nclient_id: 500\nStatus: DRAFT — awaiting approval\n\n## identity\n- business_name: Test [stated]\n"
LANGS = ("he", "en", "fr", "ar", "ru")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Base(unittest.TestCase):
    def setUp(self):
        self.db = tsq.FakeDB()
        self.patch(bb, "_db", lambda: self.db)
        bb._cache.clear()
        self.addCleanup(bb._cache.clear)
        self.alerts, self.chat, self.characterised = [], [], True
        self.patch(ba, "_alert", lambda lines: self.alerts.extend(lines))
        self.patch(ba, "_post_chat", lambda cid, body: self.chat.append((cid, body)) or True)
        self.patch(ba, "_lang", lambda cid: "he")
        self.patch(ba, "_characterised", lambda cid: self.characterised)
        self.patch(ba, "_brief_clients", lambda: [CID])

    def patch(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, old)

    def draft(self, status="draft", **more):
        self.db.tables.setdefault("app_settings", []).append({"key": f"brand_brief:{CID}", "value": {
            "versions": [{"v": 1, "at": "2026-11-20T10:00:00+00:00", "by": "intake_agent", "kind": "drafted", "status": status,
                          "text": TEXT, "palette": None, **more}]}})
        bb._cache.clear()

    def asked(self, hours_ago):
        bb.set_page_state(CID, approval_presented_at=(datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat())
        bb._cache.clear()


class WhoGrants(Base):
    def test_a_draft_is_read_and_grants_nothing(self):
        self.draft()
        self.assertEqual(bb.for_agents(CID)["status"], "draft")                 # information from the first day
        self.assertTrue(bb.for_agents(CID)["text"].startswith("# Brand Brief"))
        self.assertEqual(governance.authority(CID), {"mode": "ask", "brief_status": "draft", "granted_by": ""})
        self.assertEqual(ba.state(CID)["state"], "waiting")

    def test_the_clients_click_is_the_grant(self):
        self.draft()
        self.assertEqual(ba.approve_by_client(CID)["state"], "granted")
        bb._cache.clear()
        cur = bb.current(CID)
        self.assertEqual((cur["status"], cur["grant"], cur["by"]), ("approved", "client", "client"))
        self.assertIn("Status: APPROVED by the client — ", cur["text"])
        self.assertEqual(governance.authority(CID), {"mode": "decide", "brief_status": "approved", "granted_by": "client"})
        # a later correction keeps the grant (the approval is still the client's)
        bb.save_edit(CID, new_text=cur["text"] + "\n- note: x [stated]\n", by="manager")
        bb._cache.clear()
        self.assertTrue(bb.granted(CID)["granted"])

    def test_an_approval_from_before_the_rule_grants_nothing(self):
        self.draft(status="approved", kind="approved", by="manager:Johnny")     # the manager approved it (client 27's shape)
        self.assertEqual(bb.granted(CID), {"exists": True, "status": "approved", "granted": False, "by": "", "at": ""})
        self.assertEqual(governance.authority(CID)["mode"], "ask")
        self.assertEqual(ba.state(CID)["state"], "waiting")                     # the client is still to be asked
        with self.assertRaises(bb.BriefError):
            bb.approve(CID, by="manager", grant="manager")                      # there is no such grant

    def test_no_brief_nothing_to_approve(self):
        self.db.tables.setdefault("app_settings", []).append({"key": f"brand_brief:{CID}", "value": {"versions": []}})
        self.assertEqual(ba.view(CID, "he"), {"state": "none"})
        self.assertEqual(ba.owner_rule(CID), {"allowed": False, "reason": "no_brief"})


class OnBehalf(Base):
    def test_only_for_a_client_who_was_asked_and_stayed_silent(self):
        self.draft()
        for prepare, reason in ((lambda: None, "client_not_asked"), (lambda: self.asked(5), "client_has_time")):
            prepare()
            self.assertEqual(ba.owner_rule(CID)["reason"], reason)
            with self.assertRaises(bb.BriefError) as caught:
                ba.approve_on_behalf(CID, "Johnny")
            self.assertEqual(str(caught.exception), reason)
        self.assertEqual(bb.granted(CID)["granted"], False)
        self.asked(30)
        self.assertTrue(ba.owner_rule(CID)["allowed"])
        self.assertEqual(ba.approve_on_behalf(CID, "Johnny")["by"], "owner_on_behalf")
        bb._cache.clear()
        cur = bb.current(CID)
        self.assertEqual((cur["grant"], cur["by"]), ("owner_on_behalf", "owner:Johnny"))     # recorded as such
        self.assertIn("ON THE CLIENT'S BEHALF", cur["text"])
        self.assertIn("ON THE CLIENT'S BEHALF", self.alerts[0])
        self.assertEqual(governance.authority(CID)["granted_by"], "owner_on_behalf")
        shown = ba.view(CID, "he")                                              # ...and SAID to the client
        self.assertIn("אושר בשמכם", shown["line"])
        self.assertEqual(ba.owner_rule(CID)["reason"], "already_approved")

    def test_the_client_is_asked_once_after_the_characterisation(self):
        self.draft()
        self.characterised = False
        self.assertEqual(ba.present_due()["asked"], [])                         # not before the characterisation
        self.characterised = True
        self.assertEqual(ba.present_due()["asked"], [CID])
        self.assertEqual(len(self.chat), 1)
        self.assertTrue(bb.page_state(CID)["approval_presented_at"])
        self.assertEqual(ba.present_due()["asked"], [])                         # once
        self.assertEqual(len(self.chat), 1)

    def test_the_words_the_client_reads(self):
        self.draft()
        for lang in LANGS:
            shown = ba.view(CID, lang)
            self.assertEqual(shown["state"], "waiting")
            for text in (shown["title"], shown["text"], shown["button"]):
                self.assertEqual(cs.problems(text, lang), [], (lang, text))
        for key, row in ba.TEXT.items():
            self.assertEqual(set(row), set(LANGS), key)
            for lang in LANGS:
                self.assertEqual(cs.problems(row[lang], lang), [], (key, lang))


class DecideMeansSomething(Base):
    def setUp(self):
        super().setUp()
        self.switch, self.decided_by = False, "client"
        self.patch(governance, "_switch_on", lambda cid: self.switch)
        self.patch(governance, "_plan_decided_by", lambda cid, plan: self.decided_by)
        self.added, self.approved = {"id": "abc123", "status": "waiting"}, []
        self.patch(social_queue, "add_safely", lambda cid, *a, **k: dict(self.added))
        self.patch(social_queue, "approve", lambda cid, ids, by="client": self.approved.append((ids, by)) or [])
        self.patch(social_queue, "next_weekday", lambda day: "2026-12-01")
        self.item = {"platform": "instagram", "format": "image", "file_id": "F" * 20, "title": "t", "day": 1, "caption": "c"}

    def queue(self):
        self.approved.clear()
        content_team._queue_for_approval(CID, "p1", 0, dict(self.item))
        return list(self.approved)

    def test_off_for_everyone_until_the_owner_turns_it_on(self):
        spec = client_settings.FIELDS["publish_without_item_approval"]
        self.assertEqual((spec["type"], spec["paying"], spec["exempt"]), ("bool", False, False))
        self.draft()
        ba.approve_by_client(CID)
        bb._cache.clear()
        self.assertEqual(self.queue(), [])                                      # granted, but the switch is off: it waits

    def test_all_three_or_it_waits(self):
        self.draft()
        self.switch = True
        self.assertEqual(self.queue(), [])                                      # switch on, brief not approved by the client
        ba.approve_by_client(CID)
        bb._cache.clear()
        self.assertEqual(self.queue(), [(["abc123"], "plan:p1")])               # all three: the plan WAS the approval
        self.decided_by = "staff"                                               # the plan was approved on the client's behalf
        self.assertEqual(self.queue(), [])
        self.decided_by = "client"
        self.added = {"id": "abc123", "status": "waiting", "flags": ["concept"]}
        self.assertEqual(self.queue(), [])                                      # an item with a notice always waits
        self.added = {"id": "abc123", "status": "approved"}
        self.assertEqual(self.queue(), [])                                      # the same file already approved: nothing twice

    def test_doubt_waits(self):
        self.switch = True

        def down(cid):
            raise RuntimeError("database")
        self.patch(governance, "authority", down)
        self.assertFalse(governance.plan_is_approval(CID, "p1"))

    def test_it_approves_and_publishes_nothing(self):
        src = _read("core", "content_team.py")
        self.assertIn('AUTO_PUBLISH = {"instagram": False, "facebook": False, "tiktok": False, "youtube": False}', src)
        for call in ("meta_content_agent", "tiktok_content_agent", "youtube_content_agent", ".publish("):
            self.assertNotIn(call, src, call)

    def test_wiring(self):
        server = _read("core", "api_server.py")
        route = '@app.post("/api/client/brand-identity/approve")'
        body = server[server.index(route):][:700]
        self.assertIn("_require_session(request)", body)
        self.assertIn("brief_approval.approve_by_client(client_id)", body)
        admin = server[server.index('@app.post("/api/admin/brand-brief/{client_id}/approve")'):][:700]
        self.assertIn("brief_approval.approve_on_behalf(client_id, req.by_name)", admin)
        self.assertNotIn("brand_brief.approve(", admin)                         # the owner has no other way to approve
        from core import admin_view
        self.assertFalse(any("brand_identity_approve" in str(e) for e in admin_view.READ_ENDPOINTS))   # staff-as-client cannot
        callers = [name for name in os.listdir(os.path.join(ROOT, "core")) + os.listdir(os.path.join(ROOT, "agents"))
                   if name.endswith(".py") and "brand_brief.approve(" in _read("core" if os.path.exists(os.path.join(ROOT, "core", name)) else "agents", name)]
        self.assertEqual(callers, ["brief_approval.py"])
        doc = _read("knowledge", "client-governance.md")
        for line in ("The client approves the brief. Nobody else.", "A draft is information. Approval is authority.",
                     "Work is never blocked waiting for an approval", "off for every client until he does"):
            self.assertIn(line, doc, line)
        self.assertIn('governance.authority(client_id)["mode"]', _read("core", "post_brief.py"))
        self.assertIn("data-br-brief-approve", _read("dashboard", "client", "index.html"))
        self.assertIn("crBrApproval", _read("dashboard", "admin", "index.html"))


if __name__ == "__main__":
    unittest.main()
