"""Avatar creation without leaving uallak (2026-10-31), revised the same day
when HeyGen was retired and Higgsfield became the presenter:

1. the source kit never tells the client to create anything themselves or
   to visit another website — they send ONE photo in the chat;
2. the presenter CHARACTER is that photo (or an AI image we made for them),
   copied into avatar-source and marked — no vendor call, no public share;
   their own photo needs the likeness consent, an AI image does not;
3. the filmed-twin footage card is gone from the dashboard (core/avatar_footage
   stays as a module; its create route answers 410);
4. a client's file is public ONLY while the vendor fetches it
   (core/public_share): revoked on success and failure, verified, retried
   once, alert if it stays public — and no make_file_public in the repo is
   left without a close, except the documented Meta publishing helper.

Stdlib only; Drive, HeyGen, the DB and the alert channel are stubs.
Run from the tests folder:  python -I -B test_avatar_in_product.py -v
"""
import ast
import importlib.util
import os
import re
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_saved = {}


def _stub(name, module):
    _saved.setdefault(name, sys.modules.get(name))
    sys.modules[name] = module


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# ─── Stubs ───────────────────────────────────────────────────────────────────

ALERTS, CHAT, ACTIVITY = [], [], []
PUBLIC = set()            # file ids currently anyone-with-link
SHARED_EVER = []          # every make_file_public call, in order
REVOKE_FAILS = {}         # file id -> how many revoke attempts fail first
FILES = []                # avatar-source listing

agent_base = types.ModuleType("core.agent_base")
agent_base.agent_alert = lambda agent, issues: ALERTS.append((agent, list(issues)))
agent_base.log_step = lambda *a, **k: None
agent_base.timed_step = lambda agent, step, fn: fn()
_stub("core.agent_base", agent_base)

httpx_stub = types.ModuleType("httpx")
_stub("httpx", httpx_stub)
supabase_stub = types.ModuleType("supabase")
supabase_stub.create_client = lambda *a, **k: None
_stub("supabase", supabase_stub)

drive = types.ModuleType("core.drive_service")


def _make_public(fid):
    SHARED_EVER.append(fid)
    PUBLIC.add(fid)
    return f"https://drive.example/{fid}"


def _revoke(fid):
    if REVOKE_FAILS.get(fid, 0) > 0:
        REVOKE_FAILS[fid] -= 1
        return False          # the share is still there when read back
    PUBLIC.discard(fid)
    return True


drive.make_file_public = _make_public
drive.revoke_public = _revoke
drive.list_files = lambda folder, fields="": [dict(f) for f in FILES]
META = {}                 # file id -> Drive metadata for get_meta
UPLOADS = []              # (folder, name, mime, app_properties)
drive.get_meta = lambda fid, fields="": dict(META.get(fid, {}))
drive.download_file = lambda fid: b"image-bytes"


def _upload(folder, name, content, mime, app_properties=None):
    UPLOADS.append((folder, name, mime, dict(app_properties or {})))
    return {"id": f"copy-{len(UPLOADS)}", "webViewLink": ""}


drive.upload_bytes = _upload
_stub("core.drive_service", drive)

_stub("core.elevenlabs_service", types.ModuleType("core.elevenlabs_service"))
claude_json = types.ModuleType("core.claude_json")
claude_json.ClaudeJSONError = Exception
claude_json.safe_claude_json_call = lambda *a, **k: {}
_stub("core.claude_json", claude_json)

client_agent = types.ModuleType("agents.client_agent")
client_agent.get_client = lambda cid: {"name": "Biz", "language": "he"}
client_agent.log_communication = lambda cid, direction, channel, text: CHAT.append(text)
_stub("agents.client_agent", client_agent)
media = types.ModuleType("agents.media_agent")
media._subfolder = lambda cid, *path: "avatar-source-folder"
_stub("agents.media_agent", media)
actions = types.ModuleType("core.agent_actions")
actions._lang = lambda cid: "he"
_stub("core.agent_actions", actions)
vault = types.ModuleType("core.secrets_vault")
vault.use_vendor_key = lambda cid, platform, job="": "hf-key"
_stub("core.secrets_vault", vault)

from agents import avatar_agent as aa     # noqa: E402
from core import avatar_footage as af     # noqa: E402
from core import public_share as ps       # noqa: E402

aa._log_activity = lambda cid, action, details, result=None: ACTIVITY.append((action, details))
CONSENTS = set()
aa.has_consent = lambda cid, scope: scope in CONSENTS
aa._rows = lambda cid, action, limit=20: []
GENERATED = set()
aa.generated_image_ids = lambda cid: set(GENERATED)
ps.RETRY_PAUSE_SECONDS = 0


def _video(fid, name, slot="", size=100, at="2026-10-31T10:00:00"):
    props = {af.SLOT_PROP: slot, "uploaded_at": at} if slot else {}
    return {"id": fid, "name": name, "mimeType": "video/mp4", "size": size, "appProperties": props}


class Base(unittest.TestCase):
    def setUp(self):
        for bucket in (ALERTS, CHAT, ACTIVITY, SHARED_EVER, FILES, UPLOADS):
            bucket.clear()
        for bucket in (PUBLIC, CONSENTS, GENERATED):
            bucket.clear()
        REVOKE_FAILS.clear()
        META.clear()
        META["p1"] = {"id": "p1", "name": "me.jpg", "mimeType": "image/jpeg", "trashed": False}


# ─── 1. Source kit: one photo in the chat; nothing to do elsewhere ──────────

class SourceKit(Base):
    def test_kit_never_sends_the_client_elsewhere(self):
        prompt = aa.SOURCE_KIT_SYSTEM
        self.assertNotRegex(prompt, "(?i)heygen|higgsfield|digital.twin|training video|consent clip")
        self.assertIn("NEVER tell the client to create anything", prompt)
        self.assertIn("never name a vendor or tool", prompt)
        self.assertIn("Never mention file names", prompt)
        self.assertIn("dashboard chat", prompt)
        self.assertIn("up to 15 seconds", prompt)

    def test_kit_no_longer_carries_a_filmed_consent_sentence(self):
        source = _src("agents", "avatar_agent.py")
        self.assertNotIn("avatar_footage.consent_sentence", source)
        self.assertNotRegex(source, r"import heygen_service|heygen_service as")


# ─── 2. The character: one image, copied and marked — no vendor, no share ───

class Character(Base):
    def test_own_photo_with_consent_becomes_the_character(self):
        CONSENTS.add("likeness")
        result = aa.create_avatar(27, photo_ids=["p1"])
        self.assertTrue(result["success"])
        self.assertEqual((result["method"], result["kind"]), ("character_image", "own_photo"))
        (folder, name, mime, props), = UPLOADS
        self.assertEqual(props, {aa.CHARACTER_PROP: "own_photo"})
        self.assertTrue(name.startswith("presenter-") and name.endswith(".jpg"))
        (action, details), = ACTIVITY
        self.assertEqual(action, "avatar_ready")
        self.assertEqual((details["provider"], details["kind"], details["source_file_id"]),
                         ("higgsfield", "own_photo", "p1"))
        self.assertEqual(SHARED_EVER, [])          # nothing public: there is no vendor call here
        self.assertEqual(CHAT, [])

    def test_own_photo_without_consent_is_refused_and_nothing_is_copied(self):
        result = aa.create_avatar(27, photo_ids=["p1"])
        self.assertEqual(result["code"], "no_consent")
        self.assertEqual((UPLOADS, ACTIVITY), ([], []))

    def test_an_ai_image_we_made_needs_no_likeness_consent(self):
        GENERATED.add("p1")
        result = aa.create_avatar(27, photo_ids=["p1"])
        self.assertTrue(result["success"])
        self.assertEqual(result["kind"], "generated")
        self.assertEqual(UPLOADS[0][3], {aa.CHARACTER_PROP: "generated"})

    def test_not_an_image_is_refused(self):
        META["p1"]["mimeType"] = "video/mp4"
        CONSENTS.add("likeness")
        self.assertEqual(aa.create_avatar(27, photo_ids=["p1"])["code"], "no_photos")

    def test_only_presenter_rows_are_characters(self):
        rows = [{"details": {"avatar_id": "hg1", "avatar_name": "old twin"}, "created_at": "1"},
                {"details": {"avatar_id": "c1", "avatar_name": "me", "provider": "higgsfield",
                             "kind": "generated"}, "created_at": "2"}]
        saved, aa._rows = aa._rows, lambda cid, action, limit=20: rows
        try:
            ready = aa.list_ready_avatars(27)
        finally:
            aa._rows = saved
        self.assertEqual([(a["avatar_id"], a["kind"]) for a in ready], [("c1", "generated")])

    def test_the_old_readiness_scan_is_a_no_op(self):
        self.assertEqual(aa.run_readiness_scan()["retired"], True)


# ─── 2. Two labelled slots, filename only as the old-folder fallback ─────────

class Slots(Base):
    def test_slot_decides_whatever_the_name(self):
        chosen = af.pick([_video("t", "consent-final.mp4", "training", size=5),
                          _video("c", "IMG_0001.mov", "consent", size=900)])
        self.assertEqual((chosen["training"]["id"], chosen["consent"]["id"]), ("t", "c"))
        self.assertEqual(chosen["source"], {"training": "slot", "consent": "slot"})

    def test_newest_upload_in_a_slot_wins(self):
        chosen = af.pick([_video("old", "x.mp4", "consent", at="2026-10-30T10:00:00"),
                          _video("new", "y.mp4", "consent", at="2026-10-31T10:00:00")])
        self.assertEqual(chosen["consent"]["id"], "new")

    def test_files_already_in_drive_still_work(self):
        chosen = af.pick([_video("a", "my consent.mp4", size=10), _video("b", "talk.mp4", size=500),
                          _video("c", "short.mp4", size=50)])
        self.assertEqual((chosen["training"]["id"], chosen["consent"]["id"]), ("b", "a"))
        self.assertEqual(chosen["source"], {"training": "drive_folder", "consent": "drive_folder"})

    def test_a_slot_beats_the_filename_convention(self):
        chosen = af.pick([_video("legacy", "consent.mp4"), _video("slot", "clip.webm", "consent"),
                          _video("t", "talk.mp4", "training")])
        self.assertEqual(chosen["consent"]["id"], "slot")

    def test_upload_slot_takes_video_only_and_tags_the_file(self):
        source = _src("core", "chat_uploads.py")
        self.assertIn('if slot and kind != "video":', source)
        self.assertIn("avatar_footage.SLOT_PROP: slot", source)
        self.assertIn('"s": slot', source)
        api = _src("core", "api_server.py")
        self.assertIn('request.headers.get("x-avatar-slot")', api)
        self.assertIn('"/api/client/avatar-footage"', api)
        self.assertIn('"/api/client/avatar-footage/create"', api)


# ─── 3. The consent sentence, on screen, recorded in the browser ─────────────

class ConsentRecording(Base):
    def test_sentence_in_every_dashboard_language_without_a_vendor_name(self):
        for lang in ("he", "en", "fr", "ar", "ru"):
            sentence = af.consent_sentence(lang)
            self.assertIn(af.NAME_PLACEHOLDER[lang], sentence)
            self.assertNotRegex(sentence, "(?i)heygen|higgsfield|elevenlabs|uallak")
        self.assertEqual(af.consent_sentence("xx"), af.consent_sentence("he"))

    def test_view_hands_the_card_the_sentence_and_both_slots(self):
        view = af.view([_video("t", "a.mp4", "training")], "en")
        self.assertEqual(view["sentence"], af.consent_sentence("en"))
        self.assertIsNotNone(view["training"])
        self.assertIsNone(view["consent"])
        self.assertFalse(view["ready"])

    def test_the_filmed_twin_card_is_gone_from_the_dashboard(self):
        html = _src("dashboard", "client", "index.html")
        for gone in ('id="footageCard"', "toggleConsentRecording", "'X-Avatar-Slot': slot", "createPresenter(",
                     "/api/client/avatar-footage"):
            self.assertNotIn(gone, html)
        self.assertNotRegex(html, r"(?m)^  footage_[a-z_]+: \{")
        api = _src("core", "api_server.py")
        create = api[api.index("def client_avatar_footage_create"):]
        create = create[:create.index("\n@app.")]
        self.assertIn('_footage_refusal("retired", status=410)', create)
        self.assertNotIn("create_avatar", create)


# ─── 4. Public only while the vendor fetches ─────────────────────────────────

class ShortExposure(Base):
    def test_one_failed_close_is_retried_without_an_alert(self):
        REVOKE_FAILS["f"] = 1
        with ps.briefly_public(27, "avatar_agent", "test") as share:
            share.url("f")
        self.assertEqual(PUBLIC, set())
        self.assertEqual(ALERTS, [])

    def test_still_public_after_the_retry_alerts_the_team(self):
        REVOKE_FAILS["f"] = 2
        with ps.briefly_public(27, "avatar_agent", "avatar creation") as share:
            share.url("f")
        self.assertEqual(PUBLIC, {"f"})
        (agent, issues), = ALERTS
        self.assertIn("STILL publicly reachable", issues[0])
        self.assertIn("f", issues[0])

    def test_a_revoke_that_raises_counts_as_failed(self):
        calls = []

        def boom(fid):
            calls.append(fid)
            raise RuntimeError("drive down")
        saved, drive.revoke_public = drive.revoke_public, boom
        try:
            with ps.briefly_public(27, "a", "x") as share:
                share.url("f")
        finally:
            drive.revoke_public = saved
        self.assertEqual(calls, ["f", "f"])               # tried, then retried once
        self.assertEqual(len(ALERTS), 1)

    def test_a_half_done_share_is_still_closed(self):
        def flaky(fid):
            PUBLIC.add(fid)                                # Drive applied it, then the answer failed
            raise RuntimeError("timeout")
        saved, drive.make_file_public = drive.make_file_public, flaky
        try:
            with self.assertRaises(RuntimeError):
                with ps.briefly_public(27, "a", "x") as share:
                    share.url("f")
        finally:
            drive.make_file_public = saved
        self.assertEqual(PUBLIC, set())

    def test_consent_text_says_what_the_code_does(self):
        statement = aa.CONSENT_STATEMENTS_HE["likeness"]
        self.assertIn("רק בזמן שכל סרטון נוצר", statement)
        self.assertIn("נסגרת מיד אחרי", statement)
        self.assertNotRegex(statement, "(?i)heygen|higgsfield")
        html = _src("dashboard", "client", "index.html")
        self.assertIn("התמונה וההקלטה נפתחות לכלי רק בזמן שכל סרטון נוצר, והגישה אליהן נסגרת מיד אחרי.", html)
        line = re.search(r"^  avatar_consent_likeness: \{(.*)\},$", html, re.M).group(1)
        for words in ("only while each video is being made", "pendant la création de chaque vidéo",
                      "أثناء إنشاء كل فيديو", "только на время создания каждого видео"):
            self.assertIn(words, line)
        self.assertNotRegex(line, "(?i)heygen|higgsfield|filmed")


class RevokeReadsBack(unittest.TestCase):
    """drive_service.revoke_public itself, against a recorded httpx."""

    def setUp(self):
        self.calls, self.lists = [], []
        fake = types.ModuleType("httpx")

        class HTTPStatusError(Exception):
            def __init__(self, message, request=None, response=None):
                super().__init__(message)
                self.response = response

        class Resp:
            def __init__(self, status, body=None):
                self.status_code, self._body, self.text = status, body or {}, ""
                self.request = types.SimpleNamespace(method="X")

            def json(self):
                return self._body

        def get(url, **kw):
            self.calls.append(("GET", url))
            return Resp(200, {"permissions": self.lists.pop(0)})

        def delete(url, **kw):
            self.calls.append(("DELETE", url))
            return Resp(204)

        fake.get, fake.delete, fake.HTTPStatusError = get, delete, HTTPStatusError
        fake.Response = Resp
        keys = types.ModuleType("agents.keys_agent")
        keys.get_key = lambda name: ""
        self._saved = {n: sys.modules.get(n) for n in ("httpx", "agents.keys_agent")}
        sys.modules["httpx"], sys.modules["agents.keys_agent"] = fake, keys
        spec = importlib.util.spec_from_file_location("real_drive_service",
                                                      os.path.join(ROOT, "core", "drive_service.py"))
        self.ds = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ds)
        self.ds._headers = lambda: {}

    def tearDown(self):
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_deletes_only_anyone_permissions_and_verifies(self):
        self.lists = [[{"id": "anyoneWithLink", "type": "anyone"}, {"id": "u1", "type": "user"}],
                      [{"id": "u1", "type": "user"}]]
        self.assertTrue(self.ds.revoke_public("F"))
        self.assertEqual([m for m, _ in self.calls], ["GET", "DELETE", "GET"])
        self.assertTrue(self.calls[1][1].endswith("/F/permissions/anyoneWithLink"))

    def test_a_share_still_there_on_read_back_is_not_closed(self):
        self.lists = [[{"id": "anyoneWithLink", "type": "anyone"}],
                      [{"id": "anyoneWithLink", "type": "anyone"}]]
        self.assertFalse(self.ds.revoke_public("F"))


# ─── 5. Repo audit: no make_file_public left without a close ─────────────────

class Audit(unittest.TestCase):
    # The ONE remaining standing share, by design: an admin-only helper whose
    # URL is fed to a separate Meta publish call (business media that is being
    # published publicly anyway). Reported in the change summary.
    ALLOWED = {("agents/media_agent.py", "prepare_for_publishing"),
               ("core/public_share.py", "url")}

    def test_every_make_file_public_call_goes_through_public_share(self):
        found = set()
        for folder in ("agents", "core"):
            for name in os.listdir(os.path.join(ROOT, folder)):
                if not name.endswith(".py"):
                    continue
                path = f"{folder}/{name}"
                tree = ast.parse(_src(folder, name))
                for fn in ast.walk(tree):
                    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    for node in ast.walk(fn):
                        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                                and node.func.attr == "make_file_public"):
                            found.add((path, fn.name))
        self.assertEqual(found, self.ALLOWED)


def tearDownModule():
    for name, module in _saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


if __name__ == "__main__":
    unittest.main()
