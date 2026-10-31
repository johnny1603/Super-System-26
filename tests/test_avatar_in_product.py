"""Avatar creation without leaving uallak (2026-10-31):

1. the source kit no longer tells the client to create the avatar themselves
   on the vendor's website — the manual route is only the automatic fallback
   when the API refuses (one plain Hebrew chat line + a team alert);
2. the training and consent videos come from two labelled dashboard slots
   (core/avatar_footage, via the chat's chunked upload) — a filename is only
   the fallback for files already in the Drive folder;
3. the consent sentence is shown on screen in the client's language and
   recorded in the browser;
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
drive.get_meta = lambda fid, fields="": {}
_stub("core.drive_service", drive)

HEYGEN = {"raise": None, "calls": []}
heygen = types.ModuleType("core.heygen_service")
heygen.list_avatars = lambda key: [{"avatar_id": "stock1"}]


def _submit(key, name, training_footage_url, video_consent_url):
    # The vendor's server fetches the files DURING the call: they must be public now
    HEYGEN["calls"].append((training_footage_url, video_consent_url, set(PUBLIC)))
    if HEYGEN["raise"]:
        raise HEYGEN["raise"]
    return {"avatar_id": "new"}


heygen.submit_digital_twin = _submit
_stub("core.heygen_service", heygen)
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
vault.use_vendor_key = lambda cid, platform, job="": "hg-key"
_stub("core.secrets_vault", vault)

from agents import avatar_agent as aa     # noqa: E402
from core import avatar_footage as af     # noqa: E402
from core import public_share as ps       # noqa: E402

aa._log_activity = lambda cid, action, details, result=None: ACTIVITY.append((action, details))
aa.has_consent = lambda cid, scope: True
aa._rows = lambda cid, action, limit=20: []
ps.RETRY_PAUSE_SECONDS = 0


def _video(fid, name, slot="", size=100, at="2026-10-31T10:00:00"):
    props = {af.SLOT_PROP: slot, "uploaded_at": at} if slot else {}
    return {"id": fid, "name": name, "mimeType": "video/mp4", "size": size, "appProperties": props}


class Base(unittest.TestCase):
    def setUp(self):
        for bucket in (ALERTS, CHAT, ACTIVITY, SHARED_EVER, FILES, HEYGEN["calls"]):
            bucket.clear()
        PUBLIC.clear()
        REVOKE_FAILS.clear()
        HEYGEN["raise"] = None


# ─── 1. Source kit: our system creates it; manual only as a fallback ─────────

class SourceKit(Base):
    def test_kit_never_sends_the_client_to_create_it_themselves(self):
        prompt = aa.SOURCE_KIT_SYSTEM
        self.assertNotIn("THEMSELVES in HeyGen", prompt)
        self.assertNotIn("HeyGen's website", prompt)
        self.assertNotIn("create digital twin", prompt)
        self.assertIn("OUR SYSTEM creates the presenter", prompt)
        self.assertIn("NEVER tell the client to create it themselves", prompt)
        self.assertIn("never mention file names", prompt)
        self.assertNotIn("consent_sentence", prompt)     # the sentence is ours, not the model's

    def test_kit_doc_carries_the_same_sentence_the_dashboard_shows(self):
        source = _src("agents", "avatar_agent.py")
        self.assertIn("avatar_footage.consent_sentence(_lang(client_id))", source)

    def test_success_does_not_take_the_manual_route(self):
        FILES[:] = [_video("t1", "a.mp4", "training"), _video("c1", "b.mp4", "consent")]
        result = aa.create_avatar(27)
        self.assertEqual(result["method"], "digital_twin_api")
        self.assertEqual(CHAT, [])

    def test_plan_refusal_falls_back_with_one_hebrew_line_and_a_team_alert(self):
        FILES[:] = [_video("t1", "a.mp4", "training"), _video("c1", "b.mp4", "consent")]
        HEYGEN["raise"] = RuntimeError("heygen submit_digital_twin failed: 403 enterprise plan required")
        result = aa.create_avatar(27)
        self.assertTrue(result["success"])
        self.assertEqual(result["method"], "manual_fallback")
        self.assertEqual(CHAT, [aa.MANUAL_FALLBACK_HE])
        self.assertEqual(len(aa.MANUAL_FALLBACK_HE.split(". ")), 1)       # one line
        self.assertRegex(aa.MANUAL_FALLBACK_HE, "[֐-׿]")
        self.assertNotRegex(aa.MANUAL_FALLBACK_HE, "(?i)heygen|drive|consent")
        self.assertTrue(any("manual route" in " ".join(issues) for _, issues in ALERTS))

    def test_other_errors_are_not_mistaken_for_a_plan_refusal(self):
        FILES[:] = [_video("t1", "a.mp4", "training"), _video("c1", "b.mp4", "consent")]
        HEYGEN["raise"] = RuntimeError("heygen submit_digital_twin failed: 500 internal")
        result = aa.create_avatar(27)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "provider_error")
        self.assertEqual(CHAT, [])


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

    def test_missing_consent_is_a_plain_refusal_not_a_filename_rule(self):
        FILES[:] = [_video("t1", "talk.mp4", "training")]
        result = aa.create_avatar(27)
        self.assertEqual(result["code"], "missing_consent")
        self.assertNotIn("filename", result["message_he"])
        self.assertEqual(HEYGEN["calls"], [])
        self.assertEqual(SHARED_EVER, [])

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

    def test_dashboard_card_has_two_labelled_slots_and_a_recorder(self):
        html = _src("dashboard", "client", "index.html")
        card = html[html.index('id="footageCard"'):]
        card = card[:card.index('id="footageCreateNote"')]
        for needle in ('id="footageSlot-training"', 'id="footageSlot-consent"', 'id="footageSentence"',
                       'onclick="toggleConsentRecording()"', 'accept="video/*"'):
            self.assertIn(needle, card)
        self.assertNotIn("Drive", card)                    # no folder, no naming rule
        self.assertIn("getUserMedia({ video: { facingMode: 'user' }, audio: true })", html)
        self.assertIn("'X-Avatar-Slot': slot", html)
        self.assertIn("chunkedUpload(file, first,", html)

    def test_dashboard_strings_exist_in_five_languages(self):
        html = _src("dashboard", "client", "index.html")
        for key in ("footage_card", "footage_training", "footage_consent", "footage_consent_hint", "footage_record",
                    "footage_stop", "footage_create", "footage_submitted", "footage_camera_denied"):
            line = re.search(rf"^  {key}: \{{(.*)\}},$", html, re.M)
            self.assertIsNotNone(line, key)
            for lang in ("he:", "en:", "fr:", "ar:", "ru:"):
                self.assertIn(lang, line.group(1), key)


# ─── 4. Public only while the vendor fetches ─────────────────────────────────

class ShortExposure(Base):
    def test_twin_files_are_public_during_the_call_and_closed_after(self):
        FILES[:] = [_video("t1", "a.mp4", "training"), _video("c1", "b.mp4", "consent")]
        aa.create_avatar(27)
        (_, _, public_during), = HEYGEN["calls"]
        self.assertEqual(public_during, {"t1", "c1"})
        self.assertEqual(PUBLIC, set())

    def test_closed_on_failure_too(self):
        FILES[:] = [_video("t1", "a.mp4", "training"), _video("c1", "b.mp4", "consent")]
        for error in (RuntimeError("403 forbidden"), RuntimeError("500 boom")):
            HEYGEN["raise"] = error
            aa.create_avatar(27)
            self.assertEqual(PUBLIC, set(), str(error))

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
        self.assertIn("רק למשך היצירה", statement)
        self.assertIn("נסגרת מיד אחריה", statement)
        html = _src("dashboard", "client", "index.html")
        self.assertIn("הקבצים נפתחים לכלי רק למשך היצירה, והגישה אליהם נסגרת מיד אחריה.", html)
        line = re.search(r"^  avatar_consent_likeness: \{(.*)\},$", html, re.M).group(1)
        for words in ("only for the duration of the creation", "pendant la création", "طوال مدة الإنشاء",
                      "только на время создания"):
            self.assertIn(words, line)


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
