"""Delete / cancel in the Social queue (2026-11-24): core/social_queue.delete,
POST /api/client/social/delete, the Social tab's delete + bulk delete, and the
manager's chat card delete_social_items.

What must stay true:
- deleting removes the queue item and its approval state, nothing else — the
  media FILE is never touched (no Drive call on this path);
- a published item can be deleted from the queue view; the count says so;
- every deletion is one entry in the EXISTING deletion log (media_deletions);
- the tab: a delete on every item, a bulk delete from the selection, ONE
  confirmation stating the count, that the media stays, and the published note;
- the chat card is the manager's only (persona "general"), runs the SAME
  delete, and "everything waiting" resolves to exactly the waiting items.

Run from the repo root:  python tests/test_social_delete.py -v
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_social_queue as tsq                # noqa: E402  (stubs + in-memory app_settings)

from core import media_inventory as mi         # noqa: E402

sq, aa = tsq.sq, tsq.aa
CID = 5


def _src(*parts) -> str:
    return tsq._src(*parts)


class WithLog(tsq.Base):
    def setUp(self):
        super().setUp()
        self.saved.append((mi, "_db", mi._db))
        mi._db = lambda: self.db
        self.saved.append((aa, "_lang", aa._lang))
        aa._lang = lambda cid: "he"

    def log(self):
        return mi.deletion_log(CID)

    def three(self):
        a = self.add(tsq.FILE_A, title="ראשון")
        b = self.add(tsq.FILE_B, title="שני")
        c = self.add(tsq.FILE_C, title="שלישי")
        sq.approve(CID, [b["id"]])
        return a, b, c


class Delete(WithLog):
    def test_removes_items_and_logs_once(self):
        a, b, c = self.three()
        out = sq.delete(CID, [a["id"], b["id"]], by="client")
        self.assertEqual(sorted(d["id"] for d in out["deleted"]), sorted([a["id"], b["id"]]))
        self.assertEqual([i["id"] for i in sq.items(CID)], [c["id"]])      # the rest stays
        log = self.log()
        self.assertEqual(len(log), 1)
        self.assertEqual((log[0]["mode"], log[0]["by"], log[0]["count"]), ("queue", "client", 2))
        self.assertEqual({i["file_id"] for i in log[0]["items"]}, {tsq.FILE_A, tsq.FILE_B})

    def test_the_media_file_is_never_touched(self):
        src = _src("core", "social_queue.py")
        body = src[src.index("def delete("):src.index("def mark_published(")]
        for call in ("drive_service", "delete_file", "media_inventory.delete(", "asset_refs"):
            self.assertNotIn(call, body)

    def test_published_item_leaves_the_queue_and_is_counted(self):
        a, b, c = self.three()
        sq.mark_published(CID, b["id"])
        out = sq.delete(CID, [b["id"]])
        self.assertEqual(out["published"], 1)
        self.assertEqual(self.log()[0]["published"], 1)
        self.assertNotIn(b["id"], {i["id"] for i in sq.items(CID)})

    def test_bad_and_missing_ids(self):
        a, _, _ = self.three()
        with self.assertRaises(sq.QueueError):
            sq.delete(CID, [])
        with self.assertRaises(sq.QueueError):
            sq.delete(CID, ["NOT AN ID!"])
        out = sq.delete(CID, [a["id"], "abcdef123456"])
        self.assertEqual(out["missing"], ["abcdef123456"])
        out = sq.delete(CID, ["abcdef123456"])
        self.assertEqual(out["deleted"], [])
        self.assertEqual(len(self.log()), 1)                                 # nothing deleted = no entry

    def test_the_route(self):
        api = _src("core", "api_server.py")
        body = api[api.index('@app.post("/api/client/social/delete")'):]
        body = body[:body.index("@app.", 1)]
        self.assertIn("_social_read_only(request)", body)                    # staff "view as client" can't
        self.assertIn('social_queue.delete(client_id, req.ids, by="client")', body)
        self.assertIn('"POST /api/client/social/delete": (CHAT, "delete_social_items")', _src("core", "chat_parity.py"))

    def test_control_room_log_names_it(self):
        admin = _src("dashboard", "admin", "index.html")
        self.assertIn("queue: 'מחיקה מתור הסושיאל (המדיה נשארת)'", admin)
        self.assertIn("manager: 'המנהל בצ׳אט (אישור הלקוח)'", admin)


class TheTab(unittest.TestCase):
    html = _src("dashboard", "client", "index.html")

    def test_delete_on_every_row_and_in_the_bar(self):
        h = self.html
        self.assertEqual(h.count("acts.push(socialDeleteBtn(item))"), 3)   # media, post copy, design
        self.assertIn('id="socialSelDelete" onclick="socialOpenDelete()"', h)
        self.assertIn("on('data-soc-del', id => socialOpenDelete([id]));", h)
        self.assertIn('onclick="socialOpenBatch(socialSelectedWaiting())"', h)   # approve takes the waiting ones
        self.assertNotIn("${waiting && !ADMIN_VIEW ? `<label class=\"ml-check\">", h)   # any item can be selected

    def test_one_confirmation_with_count_media_and_published(self):
        h = self.html
        body = h[h.index("function socialOpenDelete("):h.index("function socialClearSelection(")]
        self.assertIn("t('soc_delete_title', { n: items.length })", body)
        self.assertIn("t('soc_delete_note')", body)
        self.assertIn("t('soc_delete_published', { n: published })", body)
        self.assertIn("'/api/client/social/delete'", body)
        self.assertEqual(body.count("data-go>"), 1)                          # one confirm button — no 2nd step
        self.assertNotIn("confirm(", body)
        for key in ("soc_delete", "soc_delete_selected", "soc_delete_title", "soc_delete_note",
                    "soc_delete_published", "soc_delete_confirm", "soc_deleted_done"):
            line = next(l for l in h.splitlines() if l.strip().startswith(key + ":"))
            for lang in tsq.LANGS:
                self.assertIn(f"{lang}:'", line, f"{key} needs {lang}")
        self.assertIn('לשונית "מדיה"', next(l for l in h.splitlines() if l.strip().startswith("soc_delete_note:")))


class ManagerCard(WithLog):
    def propose(self, persona, params):
        return aa.propose(CID, "client_chat", persona, {"name": "delete_social_items", "params": params},
                          client={"id": CID}, dry_run=True)

    def test_manager_only(self):
        self.three()
        self.assertTrue(self.propose("general", {"status": "waiting"}).get("dry_run"))
        for persona in ("media", "meta", "website", "ads"):
            self.assertEqual(self.propose(persona, {"status": "waiting"}), {"error": "not_allowed_here"})
        state = {}
        names = lambda persona: {a["name"] for a in aa.available(CID, "client_chat", state, client={"id": CID},
                                                                 persona=persona)}
        old = aa.ACTIONS["delete_social_items"]["available"]
        aa.ACTIONS["delete_social_items"]["available"] = lambda s, c: True
        try:
            self.assertIn("delete_social_items", names("general"))
            self.assertNotIn("delete_social_items", names("media"))
        finally:
            aa.ACTIONS["delete_social_items"]["available"] = old
        # the client chat's path: the turn's action list, filtered for the persona
        listed = [{"name": "delete_social_items"}, {"name": "approve_social_items"}]
        self.assertEqual([a["name"] for a in aa.for_persona(listed, "media")], ["approve_social_items"])
        self.assertEqual(len(aa.for_persona(listed, "general")), 2)
        src = _src("agents", "support_agent.py")
        self.assertIn('agent_actions.for_persona(part["available_actions"], persona_id)', src)

    def test_everything_waiting_means_exactly_the_waiting_items(self):
        a, b, c = self.three()                                               # b is approved
        prepared = aa._prepare_delete_social(CID, {"status": "waiting"}, {"language": "he"}, [])
        self.assertEqual(sorted(prepared["params"]["item_ids"]), sorted([a["id"], c["id"]]))
        named = aa._prepare_delete_social(CID, {"item_ids": [b["id"]]}, {"language": "he"}, [])
        self.assertEqual(named["params"]["item_ids"], [b["id"]])
        none = aa._prepare_delete_social(CID, {"status": "rejected"}, {"language": "he"}, [])
        self.assertIn("refuse", none)

    def test_card_names_count_items_and_media_stays(self):
        a, b, c = self.three()
        sq.mark_published(CID, b["id"])
        shown = aa._social_delete_display(CID, {"item_ids": [a["id"], b["id"]]})
        self.assertEqual(shown["count"], "2")
        self.assertIn("ראשון", shown["items"])
        self.assertIn("שני · פורסם", shown["items"])
        self.assertIn("אי אפשר לבטל פרסום", shown["published_note"])
        what = aa._pick(aa.ACTIONS["delete_social_items"]["what"], "he")
        self.assertIn('המדיה עצמה לא נמחקת — היא נשארת בלשונית "מדיה"', what)
        for lang in tsq.LANGS:
            self.assertIn("{published_note}", aa.ACTIONS["delete_social_items"]["what"][lang])

    def test_the_yes_runs_the_same_delete_and_logs(self):
        a, b, c = self.three()
        out = aa._run_delete_social(CID, {"item_ids": [a["id"], c["id"]]}, None)
        self.assertEqual(out["deleted"], 2)
        self.assertEqual([i["id"] for i in sq.items(CID)], [b["id"]])
        self.assertEqual((self.log()[0]["mode"], self.log()[0]["by"]), ("queue", "manager"))
        with self.assertRaises(aa.ActionFailed):
            aa._run_delete_social(CID, {"item_ids": [a["id"]]}, None)        # already gone

    def test_never_says_it_cant(self):
        does = aa.ACTIONS["delete_social_items"]["does"]
        self.assertIn("Never say you can't delete queue items from the chat", does)
        self.assertIn("status waiting", does)
        self.assertIn("media FILES are NOT deleted", does)


if __name__ == "__main__":
    unittest.main()
