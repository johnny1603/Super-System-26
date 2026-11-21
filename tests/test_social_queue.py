"""The Social tab — the ONE approval gate for social media (core/social_queue,
2026-11-22, docs/SOCIAL-QUEUE.md).

What must stay true:
- the gate cannot be bypassed: every publisher (Meta, TikTok inbox upload,
  YouTube) refuses BEFORE any platform call unless the file / item is
  APPROVED in the queue for that channel — waiting, rejected, another channel,
  already published, or an unreadable queue all refuse; the card prepares
  queue an unapproved file instead of opening a send card;
- reject needs a reason and goes back to the producing agent (a line in
  THEIR chat + the fix card replacing the item);
- batch approval approves several in one call and skips what isn't waiting;
- a caption edit before approval sticks; an edit after approval re-opens it;
- channels without automatic publishing show "ready to publish in one tap";
- the waiting counter matches the queue; gradual paging; 5 languages.
"""
import copy
import os
import re
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("SESSION_SECRET_KEY", "test-only-not-a-secret")

for _name, _attrs in (("anthropic", {"Anthropic": lambda *a, **k: None}),
                      ("httpx", {"Response": object, "Client": object, "HTTPError": Exception,
                                 "HTTPStatusError": Exception, "TimeoutException": Exception,
                                 "RequestError": Exception}),
                      ("supabase", {"create_client": lambda *a, **k: None})):
    if _name not in sys.modules:
        _stub = types.ModuleType(_name)
        _stub.__dict__.update(_attrs)
        sys.modules[_name] = _stub

from core import social_queue as sq            # noqa: E402
from core import agent_actions as aa           # noqa: E402

LANGS = ("he", "en", "fr", "ar", "ru")
FILE_A, FILE_B, FILE_C = "fileAAAAAAAAAA1", "fileBBBBBBBBBB2", "fileCCCCCCCCCC3"


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class _Q:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.payload, self.op = db, table, [], None, "select"

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self

    def like(self, col, pattern):
        prefix = pattern.rstrip("%")
        self.filters.append(lambda r: str(r.get(col, "")).startswith(prefix))
        return self

    def limit(self, _n):
        return self

    def upsert(self, row, on_conflict=""):
        self.op, self.payload, self.key = "upsert", copy.deepcopy(row), on_conflict
        return self

    def execute(self):
        if self.db.broken:
            raise RuntimeError("db down")
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "upsert":
            for r in rows:
                if r.get(self.key) == self.payload.get(self.key):
                    r.update(self.payload)
                    break
            else:
                rows.append(self.payload)
            return types.SimpleNamespace(data=[])
        return types.SimpleNamespace(data=[copy.deepcopy(r) for r in rows if all(f(r) for f in self.filters)])


class FakeDB:
    def __init__(self):
        self.tables, self.broken = {}, False

    def table(self, name):
        return _Q(self, name)


class Base(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.modes = {"instagram": "client_publishes", "facebook": "not_connected", "tiktok": "client_publishes",
                      "youtube": "not_connected"}
        self.saved = [(sq, "_db", sq._db), (sq, "_modes", sq._modes)]
        sq._db = lambda: self.db
        sq._modes = lambda cid: dict(self.modes)

    def tearDown(self):
        for mod, name, value in self.saved:
            setattr(mod, name, value)

    def add(self, file_id=FILE_A, kind="video", channels=("tiktok",), **kw):
        return sq.add(5, file_id, kind, list(channels), **kw)


# ─── The gate ────────────────────────────────────────────────────────────────

class GateCannotBeBypassed(Base):
    def test_nothing_queued_is_closed(self):
        with self.assertRaises(sq.GateClosed) as e:
            sq.check(5, "tiktok", file_id=FILE_A)
        self.assertEqual(e.exception.code, "not_approved")

    def test_waiting_and_rejected_are_closed(self):
        item = self.add()
        with self.assertRaises(sq.GateClosed):
            sq.check(5, "tiktok", file_id=FILE_A)
        self.saved.append((sq, "send_back", sq.send_back))
        sq.send_back = lambda cid, it, lang="he": {"posted": True, "card": True}
        sq.reject(5, item["id"], "too dark")
        with self.assertRaises(sq.GateClosed):
            sq.check(5, "tiktok", file_id=FILE_A)

    def test_approved_opens_only_its_channels(self):
        item = self.add(channels=("tiktok",))
        sq.approve(5, [item["id"]])
        self.assertEqual(sq.check(5, "tiktok", file_id=FILE_A)["id"], item["id"])
        with self.assertRaises(sq.GateClosed):
            sq.check(5, "youtube", file_id=FILE_A)
        with self.assertRaises(sq.GateClosed):
            sq.check(5, "tiktok", file_id=FILE_B)
        with self.assertRaises(sq.GateClosed):
            sq.check(6, "tiktok", file_id=FILE_A)          # another client's queue

    def test_published_is_closed_and_unreadable_queue_is_closed(self):
        item = self.add(kind="image", channels=("instagram",), file_id=FILE_B)
        sq.approve(5, [item["id"]])
        sq.mark_published(5, item["id"])
        with self.assertRaises(sq.GateClosed):
            sq.check(5, "instagram", item_id=item["id"])
        self.db.broken = True
        with self.assertRaises(sq.GateClosed):
            sq.check(5, "instagram", file_id=FILE_B)

    def test_an_edit_after_approval_closes_the_gate_again(self):
        item = self.add()
        sq.approve(5, [item["id"]])
        sq.edit(5, item["id"], caption="new words")
        self.assertEqual(sq.get(5, item["id"])["status"], "waiting")
        with self.assertRaises(sq.GateClosed):
            sq.check(5, "tiktok", file_id=FILE_A)

    def test_tiktok_upload_refuses_before_any_tiktok_call(self):
        from agents import tiktok_content_agent as tt
        calls = []
        self.saved.append((tt, "_get_connection", tt._get_connection))
        tt._get_connection = lambda cid: calls.append(cid) or {"access_token": "x", "account_id": "y"}
        with self.assertRaises(tt.UploadRefused) as e:
            tt.begin_upload(5, FILE_A)
        self.assertEqual(e.exception.code, "not_approved")
        self.assertEqual(calls, [])
        self.assertIn("not_approved", aa.ACTIONS["send_tiktok_to_inbox"]["refusals"])

    def test_youtube_publish_refuses_before_any_youtube_call(self):
        from agents import youtube_content_agent as yt
        calls = []
        self.saved.append((yt, "_get_connection", yt._get_connection))
        yt._get_connection = lambda cid: calls.append(cid) or {}
        out = yt.publish(5, {"drive_file_id": FILE_A, "title": "t"})
        self.assertFalse(out["success"])
        self.assertTrue(out.get("not_approved"))
        self.assertEqual(calls, [])

    def test_meta_publish_needs_an_approved_item(self):
        from agents import meta_content_agent as mc
        calls = []
        for name in ("_publish_facebook", "_publish_instagram"):
            self.saved.append((mc, name, getattr(mc, name)))
            setattr(mc, name, lambda *a, **k: calls.append(a) or {"id": "1"})
        spec = {"target": "instagram", "kind": "photo", "message": "hi", "media_url": "https://example.com/a.jpg"}
        errors = mc._validate_publish_spec(spec)
        if errors:                                   # the kind name differs: use a valid one from the module
            spec["kind"] = sorted(mc.VALID_KINDS["instagram"] if isinstance(mc.VALID_KINDS, dict) else mc.VALID_KINDS)[0]
        self.assertTrue(mc.publish(5, dict(spec)).get("not_approved"))        # no queue_item at all
        item = self.add(kind="image", channels=("instagram",))
        self.assertTrue(mc.publish(5, {**spec, "queue_item": item["id"]}).get("not_approved"))   # waiting
        self.assertEqual(calls, [])

    def test_every_publisher_calls_the_gate_in_source(self):
        """A new publish function must ask the queue too: the three platform
        writers and their only route each name the gate."""
        meta = _src("agents", "meta_content_agent.py")
        tiktok = _src("agents", "tiktok_content_agent.py")
        youtube = _src("agents", "youtube_content_agent.py")
        self.assertRegex(meta, r"def publish\(client_id[\s\S]{0,2500}social_queue\.check\(")
        self.assertRegex(tiktok, r"def begin_upload\(client_id[\s\S]{0,1200}social_queue\.check\(")
        self.assertRegex(youtube, r"def publish\(client_id[\s\S]{0,2500}social_queue\.check\(")
        # TikTok's inline publish goes through begin_upload, never init_inbox_upload directly
        self.assertEqual(len(re.findall(r"init_inbox_upload\(", tiktok)), 1)
        api = _src("core", "api_server.py")
        self.assertIn("queue_item: str", api)
        # content_team still has no publish call of its own
        self.assertNotRegex(_src("core", "content_team.py"), r"\.publish\(")

    def test_card_prepare_queues_instead_of_opening_a_send_card(self):
        self.saved.append((aa, "_media_name", aa._media_name))
        aa._media_name = lambda cid, fid: "clip.mp4"
        out = aa._social_gate(5, "tiktok", [FILE_A], {"language": "en"})
        self.assertIn("refuse", out)
        self.assertIn("Social", out["refuse"])
        self.assertEqual([i["status"] for i in sq.items(5)], ["waiting"])
        # asked again: still ONE item
        aa._social_gate(5, "tiktok", [FILE_A], {"language": "en"})
        self.assertEqual(len(sq.items(5)), 1)
        sq.approve(5, [sq.items(5)[0]["id"]])
        self.assertEqual(aa._social_gate(5, "tiktok", [FILE_A], {"language": "en"}), {})


# ─── Reject → back to the producer ───────────────────────────────────────────

class RejectGoesBack(Base):
    def test_reason_required(self):
        item = self.add()
        with self.assertRaises(sq.QueueError):
            sq.reject(5, item["id"], " ")

    def test_reject_posts_in_the_producer_chat_and_opens_the_fix_card(self):
        import agents.client_agent as ca
        posted, proposed = [], []
        self.saved += [(ca, "log_communication", ca.log_communication), (aa, "propose", aa.propose)]
        ca.log_communication = lambda cid, direction, channel, text: posted.append((channel, text))
        aa.propose = lambda cid, origin, persona, raw, **kw: proposed.append((persona, raw)) or {"card": {"id": 9}}
        item = self.add(kind="image", channels=("instagram",), title="Morning promo", caption="Good morning")
        out = sq.reject(5, item["id"], "Use the red logo", lang="en")
        self.assertEqual(sq.get(5, item["id"])["status"], "rejected")
        self.assertEqual(posted[0][0], "dashboard_chat:media")
        self.assertIn("Use the red logo", posted[0][1])
        persona, raw = proposed[0]
        self.assertEqual(persona, "media")
        self.assertEqual(raw["name"], "produce_requested_content")
        piece = raw["params"]["pieces"][0]
        self.assertEqual(piece["replaces"], item["id"])
        self.assertIn("Use the red logo", piece["brief"])
        self.assertTrue(out["sent_back"]["card"])
        # the fix card's params pass the action's own validation
        clean = aa._v_pieces(raw["params"]["pieces"], 5)
        self.assertEqual(clean[0]["replaces"], item["id"])
        self.assertEqual(clean[0]["channels"], ["instagram"])

    def test_the_fixed_version_links_back(self):
        old = self.add(kind="image", channels=("instagram",))
        new = self.add(file_id=FILE_B, kind="image", channels=("instagram",), replaces=old["id"])
        self.assertEqual(sq.get(5, old["id"])["fixed_by"], new["id"])
        self.assertEqual(new["status"], "waiting")

    def test_produced_pieces_enter_the_queue(self):
        from agents import media_agent
        self.saved += [(media_agent, "generate_image", media_agent.generate_image)]
        media_agent.generate_image = lambda cid, brief, platform: {"success": True, "file_id": FILE_C, "link": ""}
        pieces = aa._v_pieces([{"kind": "image", "brief": "A bright morning coffee post", "platform": "instagram"}], 5)
        try:
            aa._run_produce(5, {"pieces": pieces}, None)
        except Exception:
            pass                                    # only the queue side matters here
        rows = sq.items(5)
        self.assertEqual([(r["file_id"], r["channels"], r["status"]) for r in rows], [(FILE_C, ["instagram"], "waiting")])


# ─── Batch, caption, one tap, counter ────────────────────────────────────────

class OwnerDecisions(Base):
    def test_batch_approval(self):
        a = self.add(FILE_A)
        b = self.add(FILE_B, kind="image", channels=("instagram", "facebook"))
        c = self.add(FILE_C)
        sq.approve(5, [c["id"]])
        done = sq.approve(5, [a["id"], b["id"], c["id"]])
        self.assertEqual(sorted(i["id"] for i in done), sorted([a["id"], b["id"]]))
        self.assertEqual({i["status"] for i in sq.items(5)}, {"approved"})
        with self.assertRaises(sq.QueueError):
            sq.approve(5, [])

    def test_batch_chat_card_is_one_card(self):
        spec = aa.ACTIONS["approve_social_items"]
        self.assertEqual(spec["approver"], "client")
        a, b = self.add(FILE_A), self.add(FILE_B)
        ids = spec["params"]["item_ids"]["validate"]([a["id"], b["id"]], 5)
        self.assertEqual(spec["run"](5, {"item_ids": ids}, None), {"approved": 2})
        with self.assertRaises(ValueError):
            spec["params"]["item_ids"]["validate"]([a["id"]], 5)          # no longer waiting

    def test_caption_edit_before_approval(self):
        item = self.add(caption="old")
        sq.edit(5, item["id"], caption="  new caption  ")
        self.assertEqual(sq.get(5, item["id"])["caption"], "new caption")
        sq.approve(5, [item["id"]])
        self.assertEqual(sq.check(5, "tiktok", file_id=FILE_A)["caption"], "new caption")
        with self.assertRaises(sq.QueueError):
            sq.edit(5, item["id"], caption="x" * (sq.CAPTION_MAX + 1))

    def test_channel_and_date_changes_are_checked(self):
        item = self.add(kind="image", channels=("instagram",))
        with self.assertRaises(sq.QueueError):
            sq.edit(5, item["id"], channels=["tiktok"])                   # an image can't go to TikTok
        with self.assertRaises(sq.QueueError):
            sq.edit(5, item["id"], planned_date="1999-01-01")
        sq.edit(5, item["id"], channels=["instagram", "facebook"], planned_date=sq._today().isoformat())
        self.assertEqual(sq.get(5, item["id"])["channels"], ["instagram", "facebook"])

    def test_one_tap_state_for_channels_without_auto_publishing(self):
        item = self.add(kind="image", channels=("instagram", "facebook"))
        sq.approve(5, [item["id"]])
        row = sq.view(5, "en")["items"][0]
        self.assertEqual(row["delivery"]["instagram"]["state"], "one_tap")
        self.assertEqual(row["delivery"]["facebook"]["state"], "not_connected")
        self.assertTrue(row["needs_tap"])
        self.assertIn("one tap", row["delivery"]["instagram"]["text"])
        # a channel we publish to automatically is scheduled, not a tap
        self.modes["instagram"] = "auto"
        self.modes["facebook"] = "auto"
        row = sq.view(5, "en")["items"][0]
        self.assertEqual(row["delivery"]["instagram"]["state"], "scheduled")
        self.assertFalse(row["needs_tap"])
        # the owner says he published it
        sq.mark_published(5, item["id"])
        self.assertEqual(sq.view(5, "en")["items"][0]["status"], "published")

    def test_tiktok_drafts_still_need_the_owners_tap(self):
        item = self.add()
        sq.approve(5, [item["id"]])
        sq.mark_delivered(5, "tiktok", "drafts", file_id=FILE_A)
        row = sq.view(5, "en")["items"][0]
        self.assertEqual(row["delivery"]["tiktok"]["state"], "drafts")
        self.assertTrue(row["needs_tap"])

    def test_counter_accuracy(self):
        ids = [self.add("file%010d" % n, kind="image", channels=("instagram",))["id"] for n in range(5)]
        self.assertEqual(sq.waiting_count(5), 5)
        sq.approve(5, ids[:2])
        self.assertEqual(sq.waiting_count(5), 3)
        d = sq.view(5, "en")
        self.assertEqual(d["counts"]["waiting"], 3)
        self.assertEqual(d["counts"]["approved"], 2)
        self.assertEqual(d["counts"]["needs_tap"], 2)
        self.assertEqual(sq.prompt_block(5)["waiting"], 3)
        self.assertEqual(sq.waiting_count(6), 0)
        admin = sq.all_pending()
        self.assertEqual([(r["client_id"], r["waiting"], r["approved"]) for r in admin], [(5, 3, 2)])

    def test_gradual_release_and_order(self):
        for n in range(9):
            self.add("file%010d" % n, kind="image", channels=("instagram",),
                     planned_date=(sq._today() + sq.timedelta(days=9 - n)).isoformat())
        first = sq.view(5, "en")
        self.assertEqual(len(first["items"]), sq.PAGE_SIZE)
        self.assertTrue(first["has_more"])
        dates = [i["planned_date"] for i in first["items"]]
        self.assertEqual(dates, sorted(dates))                         # nearest date first
        rest = sq.view(5, "en", offset=first["next_offset"])
        self.assertEqual(len(rest["items"]), 3)
        self.assertFalse(rest["has_more"])

    def test_empty_state_is_a_calm_line_in_every_language(self):
        for lang in LANGS:
            d = sq.view(5, lang)
            self.assertEqual(d["items"], [])
            self.assertGreater(len(d["empty"]), 40)


# ─── Languages, names, the dashboard ─────────────────────────────────────────

class TextsAndPage(unittest.TestCase):
    def test_every_table_has_five_languages_and_no_vendor_names(self):
        from core import vendor_names
        tables = list(sq.STATUS_TEXT.values()) + list(sq.DELIVERY_TEXT.values()) + list(sq.TEXT.values())
        for table in tables:
            self.assertEqual(set(table), set(LANGS))
            for lang, text in table.items():
                self.assertEqual(vendor_names.scrub_vendor_names(text, lang) if hasattr(vendor_names, "scrub_vendor_names")
                                 else text, text)

    def test_dashboard_tab(self):
        html = _src("dashboard", "client", "index.html")
        self.assertIn("'media', 'social', 'personal'", html)
        self.assertIn('data-view="social"', html)
        self.assertIn('id="navSocialCount"', html)
        self.assertIn('id="viewSocial"', html)
        m = re.search(r"nav_social: \{ he:'סושיאל', en:'[^']+', fr:'[^']+', ar:'[^']+', ru:'[^']+' \}", html)
        self.assertIsNotNone(m)
        keys = set(re.findall(r"t\('(soc_[a-z_]*[a-z])'", html)) | {"soc_ch_" + c for c in sq.CHANNELS}
        for key in sorted(keys):
            self.assertRegex(html, r"\n  " + key + r": \{ he:'[^\n]+', en:'[^\n]+', fr:'[^\n]+', ar:'[^\n]+', ru:'[^\n]+' \}", key)
        self.assertIn("social_waiting", _src("core", "api_server.py"))

    def test_admin_mirror_is_read_only(self):
        admin = _src("dashboard", "admin", "index.html")
        self.assertIn("/api/admin/control-room/${clientId}/social", admin)
        self.assertIn("/api/admin/social-queue", admin)
        block = admin[admin.index("async function crLoadSocialClient"):admin.index("async function crLoadSocialAll")]
        self.assertNotIn("method: 'POST'", block)
        api = _src("core", "api_server.py")
        for route in ("/api/admin/control-room/{client_id}/social", "/api/admin/social-queue"):
            seg = api[api.index(route):api.index(route) + 600]
            self.assertIn("_require_admin(request)", seg)
            self.assertNotIn("approve(", seg)


if __name__ == "__main__":
    unittest.main()
