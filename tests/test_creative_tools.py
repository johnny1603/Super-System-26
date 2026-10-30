"""Separate creative-tool connections, the video provider choice and the
voice recordings (core/vendor_tools, core/avatar_video, core/voice_recordings,
core/elevenlabs_service, the connect_creative_tool card, client_state and the
dashboard cards).

Stdlib only — httpx, supabase, the client store, Drive, HeyGen and the vault
are stubs in sys.modules; every HTTP call is RECORDED so the tests can prove
what a key check sends (one read-only GET) and that a rejected key is never
stored.

Run from the tests folder:  python -I -B test_creative_tools.py -v
"""
import os
import struct
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# ─── Stubs (installed before anything imports them) ──────────────────────────

class FakeResponse:
    def __init__(self, status_code=200, body=None, content=b""):
        self.status_code, self._body, self.content = status_code, body or {}, content
        self.text = "SECRET-BODY-NEVER-SHOWN"

    def json(self):
        return self._body


class RecordingHttpx(types.ModuleType):
    def __init__(self):
        super().__init__("httpx")
        self.calls, self.next = [], []

    def _answer(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        answer = self.next.pop(0) if self.next else FakeResponse()
        if isinstance(answer, Exception):
            raise answer
        return answer

    def get(self, url, **kwargs):
        return self._answer("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._answer("POST", url, kwargs)

    def delete(self, url, **kwargs):
        return self._answer("DELETE", url, kwargs)

    class Response:
        pass


HTTPX = RecordingHttpx()
sys.modules["httpx"] = HTTPX
supabase_stub = types.ModuleType("supabase")
supabase_stub.create_client = lambda *a, **k: None
sys.modules.setdefault("supabase", supabase_stub)

# The client store: one row per (client, platform), key kept only here
ACCOUNTS = {}
client_agent = types.ModuleType("agents.client_agent")
client_agent.get_accounts = lambda cid: [
    {"platform": p, "status": "active", "has_secret": True, "secret_last4": k[-4:]}
    for (c, p), k in ACCOUNTS.items() if c == cid]


def _upsert(cid, platform, account_id="", secret="", status="active"):
    ACCOUNTS[(cid, platform)] = secret


def _remove(cid, platforms=None):
    gone = [p for p in (platforms or []) if ACCOUNTS.pop((cid, p), None) is not None]
    return len(gone)


client_agent.upsert_account = _upsert
client_agent.remove_accounts = _remove
client_agent._db = lambda: None
sys.modules["agents.client_agent"] = client_agent

CONSENTS = set()      # (client_id, scope)
CLONES = {}           # client_id -> voice_id
avatar_stub = types.ModuleType("agents.avatar_agent")
avatar_stub.has_consent = lambda cid, scope: (cid, scope) in CONSENTS
avatar_stub.record_consent = lambda cid, scope: CONSENTS.add((cid, scope)) or {"success": True}
avatar_stub.current_voice_id = lambda cid: CLONES.get(cid, "")
avatar_stub.list_ready_avatars = lambda cid: [{"avatar_id": "av1", "avatar_name": "Me"}]
avatar_stub._source_files = lambda cid: []
sys.modules["agents.avatar_agent"] = avatar_stub

vault = types.ModuleType("core.secrets_vault")
vault.use_vendor_key = lambda cid, service, job="": ACCOUNTS.get((cid, service), "")
sys.modules["core.secrets_vault"] = vault

DRIVE_FILES = {}      # folder -> [file dicts]
DRIVE_CALLS = []
drive_stub = types.ModuleType("core.drive_service")


def _upload_bytes(folder, name, content, mime, app_properties=None):
    f = {"id": f"f{len(DRIVE_CALLS)}", "name": name, "mimeType": mime, "size": len(content),
         "appProperties": app_properties or {}}
    DRIVE_FILES.setdefault(folder, []).append(f)
    DRIVE_CALLS.append(("upload", folder, name))
    return f


drive_stub.upload_bytes = _upload_bytes
drive_stub.list_files = lambda folder, fields="": [dict(f) for f in DRIVE_FILES.get(folder, [])]
drive_stub.trash_file = lambda fid: DRIVE_CALLS.append(("trash", fid))
drive_stub.make_file_public = lambda fid: f"https://drive.example/{fid}"
drive_stub.download_file = lambda fid: b"audio"
sys.modules["core.drive_service"] = drive_stub

media_stub = types.ModuleType("agents.media_agent")
media_stub._subfolder = lambda cid, *path: f"client{cid}/" + "/".join(path)
sys.modules["agents.media_agent"] = media_stub

HEYGEN_CALLS = []
heygen_stub = types.ModuleType("core.heygen_service")
heygen_stub.API_BASE = "https://api.heygen.example"
heygen_stub.generate_avatar_video = lambda key, avatar, script, voice_id="", audio_url="": (
    HEYGEN_CALLS.append((avatar, voice_id, audio_url)) or "vid1")
heygen_stub.wait_for_video = lambda key, vid: {"video_url": "https://heygen/v.mp4", "duration": 30}
sys.modules["core.heygen_service"] = heygen_stub

from core import avatar_video as av            # noqa: E402
from core import elevenlabs_service as el      # noqa: E402
from core import vendor_tools as vt            # noqa: E402
from core import voice_recordings as vr        # noqa: E402

KEY_H, KEY_E, KEY_HF = "hg-key-0000-AAAA", "el-key-1111-BBBB", "hf-key-2222-CCCC"


class ToolCase(unittest.TestCase):
    def setUp(self):
        ACCOUNTS.clear()
        CONSENTS.clear()
        CLONES.clear()
        DRIVE_FILES.clear()
        DRIVE_CALLS.clear()
        HEYGEN_CALLS.clear()
        HTTPX.calls.clear()
        HTTPX.next.clear()
        self.records, self.logs = {}, []
        self.saved = (vt._record, vt._save, vt._log)
        vt._record = lambda cid: dict(self.records.get(cid, {}))
        vt._save = lambda cid, rec: self.records.__setitem__(cid, dict(rec))
        vt._log = lambda cid, action, tool, extra=None: self.logs.append((cid, action, tool, extra))

    def tearDown(self):
        vt._record, vt._save, vt._log = self.saved


# ─── 1. Each tool is its own connection ──────────────────────────────────────

class SeparateConnections(ToolCase):
    def test_heygen_needs_likeness_consent_elevenlabs_does_not(self):
        with self.assertRaises(vt.ToolError) as e:
            vt.connect(27, "heygen", KEY_H)
        self.assertEqual(e.exception.code, "consent_required")
        self.assertEqual(ACCOUNTS, {})
        self.assertEqual(HTTPX.calls, [])                 # refused before any vendor call
        vt.connect(27, "elevenlabs", KEY_E)              # no consent needed to connect
        self.assertIn((27, "elevenlabs"), ACCOUNTS)

    def test_heygen_consent_given_at_connect_is_recorded(self):
        result = vt.connect(27, "heygen", KEY_H, consent=True)
        self.assertEqual(result["status"], "connected")
        self.assertIn((27, "likeness"), CONSENTS)

    def test_one_connected_without_the_other(self):
        vt.connect(27, "elevenlabs", KEY_E)
        self.assertEqual(vt.status(27, "elevenlabs"), "connected")
        self.assertEqual(vt.status(27, "heygen"), "not_connected")
        self.assertEqual(vt.missing(27, ["heygen", "elevenlabs"]), ["heygen"])

    def test_disconnect_removes_only_that_tool(self):
        CONSENTS.add((27, "likeness"))
        vt.connect(27, "heygen", KEY_H)
        vt.connect(27, "elevenlabs", KEY_E)
        vt.connect(27, "higgsfield", KEY_HF)
        vt.disconnect(27, "heygen")
        self.assertNotIn((27, "heygen"), ACCOUNTS)
        self.assertIn((27, "elevenlabs"), ACCOUNTS)
        self.assertIn((27, "higgsfield"), ACCOUNTS)
        self.assertIn((27, "likeness"), CONSENTS)        # consent stays; only the key goes
        self.assertNotIn("heygen", self.records[27]["health"])
        self.assertIn("elevenlabs", self.records[27]["health"])

    def test_rejected_key_is_never_stored(self):
        HTTPX.next.append(FakeResponse(401, {"detail": {"status": "invalid_api_key"}}))
        with self.assertRaises(vt.ToolError) as e:
            vt.connect(27, "elevenlabs", KEY_E)
        self.assertEqual(e.exception.code, "key_rejected")
        self.assertEqual(ACCOUNTS, {})

    def test_key_check_is_one_read_only_get(self):
        vt.connect(27, "elevenlabs", KEY_E)
        (method, url, kwargs), = HTTPX.calls
        self.assertEqual(method, "GET")
        self.assertTrue(url.endswith("/v1/user"))
        self.assertEqual(kwargs["headers"], {"xi-api-key": KEY_E})

    def test_restricted_elevenlabs_key_still_counts_as_real(self):
        HTTPX.next.append(FakeResponse(401, {"detail": {"status": "missing_permissions"}}))
        self.assertEqual(vt.connect(27, "elevenlabs", KEY_E)["status"], "connected")

    def test_unreachable_vendor_stores_as_unverified(self):
        HTTPX.next.append(ConnectionError("down"))
        self.assertEqual(vt.connect(27, "elevenlabs", KEY_E)["status"], "connected_unverified")
        self.assertEqual(vt.status(27, "elevenlabs"), "connected_unverified")

    def test_higgsfield_has_no_key_check(self):
        self.assertEqual(vt.connect(27, "higgsfield", KEY_HF)["status"], "connected_unverified")
        self.assertEqual(HTTPX.calls, [])

    def test_empty_whitespace_and_unknown(self):
        for bad in ("", "   ", "two words"):
            with self.assertRaises(vt.ToolError) as e:
                vt.connect(27, "elevenlabs", bad)
            self.assertEqual(e.exception.code, "empty_key")
        with self.assertRaises(vt.ToolError) as e:
            vt.connect(27, "creatomate", KEY_E)
        self.assertEqual(e.exception.code, "unknown_tool")

    def test_legacy_rows_keep_working_without_reentry(self):
        # Connected before this module: a row, no health record
        ACCOUNTS[(27, "heygen")] = KEY_H
        ACCOUNTS[(27, "elevenlabs")] = KEY_E
        self.assertEqual(vt.status(27, "heygen"), "connected_unverified")
        self.assertTrue(vt.is_connected(27, "heygen"))
        self.assertEqual(vt.missing(27, ["heygen", "elevenlabs"]), [])

    def test_key_rejected_during_a_job_shows_on_the_card(self):
        vt.connect(27, "elevenlabs", KEY_E)
        vt.mark_rejected(27, "elevenlabs")
        self.assertEqual(vt.status(27, "elevenlabs"), "key_rejected")
        self.assertFalse(vt.is_connected(27, "elevenlabs"))

    def test_test_reports_rejected_and_ok(self):
        vt.connect(27, "elevenlabs", KEY_E)
        HTTPX.next.append(FakeResponse(401, {"detail": {"status": "invalid_api_key"}}))
        self.assertEqual(vt.test(27, "elevenlabs")["status"], "key_rejected")
        self.assertEqual(vt.test(27, "elevenlabs")["status"], "connected")
        with self.assertRaises(vt.ToolError):
            vt.test(27, "heygen")                          # not connected

    def test_statuses_never_carry_a_key(self):
        CONSENTS.add((27, "likeness"))
        vt.connect(27, "heygen", KEY_H)
        view = vt.statuses(27)
        self.assertEqual(set(view), {"heygen", "elevenlabs", "higgsfield"})
        self.assertNotIn(KEY_H, repr(view))
        self.assertNotIn("last4", view["heygen"])               # not even part of it
        self.assertNotIn(KEY_H[-4:], repr(view))
        self.assertNotIn(KEY_H, repr(self.records) + repr(self.logs))

    def test_consent_belongs_to_its_tool(self):
        self.assertEqual(vt.TOOLS["heygen"]["consent"], "likeness")
        self.assertTrue(vt.TOOLS["heygen"]["consent_required"])
        self.assertEqual(vt.TOOLS["elevenlabs"]["consent"], "voice")
        self.assertFalse(vt.TOOLS["elevenlabs"]["consent_required"])
        self.assertIsNone(vt.TOOLS["higgsfield"]["consent"])


class MissingToolMessage(unittest.TestCase):
    def test_names_exactly_the_missing_tool(self):
        block = vt.missing_block(["elevenlabs"])
        self.assertEqual(block["code"], "missing_tool")
        # Named by what the client reads (the capability), never the vendor
        self.assertIn("הקול שלך", block["message_he"])
        self.assertNotIn("הדמות", block["message_he"])
        self.assertEqual(block["buttons"], [{"tool": "elevenlabs", "label": "לחיבור \"הקול שלך\"",
                                             "focus": "toolCard-elevenlabs"}])

    def test_two_missing_tools_two_buttons(self):
        block = vt.missing_block(["heygen", "elevenlabs"])
        self.assertIn("דמות מצולמת שלך", block["message_he"])
        self.assertIn("הקול שלך", block["message_he"])
        self.assertEqual([b["focus"] for b in block["buttons"]], ["toolCard-heygen", "toolCard-elevenlabs"])


# ─── 2. Provider choice ──────────────────────────────────────────────────────

class ProviderChoice(ToolCase):
    def setUp(self):
        super().setUp()
        os.environ.pop("HIGGSFIELD_AVATAR_VIDEO_ENABLED", None)

    def test_default_and_choice(self):
        self.assertEqual(vt.default_provider(27), "heygen")
        vt.set_default_provider(27, "higgsfield")
        self.assertEqual(vt.default_provider(27), "higgsfield")
        with self.assertRaises(vt.ToolError) as e:
            vt.set_default_provider(27, "sora")
        self.assertEqual(e.exception.code, "unknown_provider")

    def test_higgsfield_is_off_until_a_live_test(self):
        ACCOUNTS[(27, "higgsfield")] = KEY_HF
        result = av.make_video(27, "שלום", {"kind": "recording", "file_id": "x"}, provider="higgsfield")
        self.assertEqual(result["code"], "provider_not_ready")
        view = {p["id"]: p for p in av.providers_view(27)}
        self.assertFalse(view["higgsfield"]["ready"])
        self.assertTrue(view["heygen"]["ready"])

    def test_chosen_provider_not_connected_names_it(self):
        result = av.make_video(27, "שלום", None, provider="heygen", heygen_voice_id="stock1")
        self.assertEqual(result["code"], "missing_tool")
        self.assertEqual(result["missing"], ["heygen"])

    def test_clone_needs_elevenlabs_named_alone(self):
        ACCOUNTS[(27, "heygen")] = KEY_H
        result = av.make_video(27, "שלום", {"kind": "clone"})
        self.assertEqual(result["missing"], ["elevenlabs"])
        self.assertIn("הקול שלך", result["message_he"])

    def test_recording_needs_no_cloning_consent(self):
        ACCOUNTS[(27, "heygen")] = KEY_H
        vr_folder = "client27/voice"
        DRIVE_FILES[vr_folder] = [{"id": "rec1", "name": "me.mp3", "mimeType": "audio/mpeg", "size": 10,
                                   "appProperties": {"uploaded_at": "2026-10-30T10:00:00+00:00"}}]
        self.assertNotIn((27, "voice"), CONSENTS)
        result = av.make_video(27, "", {"kind": "recording", "file_id": "rec1"})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["voice_kind"], "recording")
        self.assertEqual(HEYGEN_CALLS, [("av1", "", "https://drive.example/rec1")])

    def test_recording_of_another_folder_refused(self):
        ACCOUNTS[(27, "heygen")] = KEY_H
        result = av.make_video(27, "", {"kind": "recording", "file_id": "someone-elses"})
        self.assertEqual(result["code"], "recording_not_found")
        self.assertEqual(HEYGEN_CALLS, [])

    def test_clone_without_consent_refused_before_any_tts(self):
        ACCOUNTS[(27, "heygen")] = KEY_H
        ACCOUNTS[(27, "elevenlabs")] = KEY_E
        CLONES[27] = "voice1"
        result = av.make_video(27, "שלום", {"kind": "clone"})
        self.assertEqual(result["code"], "no_voice_consent")
        self.assertEqual(result["buttons"][0]["focus"], "toolCard-elevenlabs")
        self.assertEqual(HTTPX.calls, [])

    def test_same_voice_sources_for_either_provider(self):
        self.assertEqual(av.VOICE_KINDS, ("recording", "clone"))
        self.assertEqual(av.make_video(27, "", {"kind": "whisper"})["code"], "bad_voice")


# ─── 3. ElevenLabs errors: plain Hebrew, never the key or the body ──────────

class ElevenLabsErrors(unittest.TestCase):
    def test_error_code_parsed_and_body_dropped(self):
        HTTPX.next[:] = [FakeResponse(401, {"detail": {"status": "quota_exceeded", "message": KEY_E}})]
        with self.assertRaises(el.ElevenLabsError) as e:
            el.list_voices(KEY_E)
        self.assertEqual((e.exception.status, e.exception.code), (401, "quota_exceeded"))
        self.assertNotIn(KEY_E, str(e.exception))
        self.assertNotIn("SECRET-BODY", str(e.exception))

    def test_plain_hebrew_for_every_case(self):
        cases = [el.ElevenLabsError(401, "invalid_api_key"), el.ElevenLabsError(401, ""),
                 el.ElevenLabsError(402, "quota_exceeded"), el.ElevenLabsError(404, ""),
                 el.ElevenLabsError(422, ""), el.ElevenLabsError(429, ""), el.ElevenLabsError(503, ""),
                 el.ElevenLabsError(418, "weird"), RuntimeError(KEY_E)]
        for error in cases:
            text = el.plain_he(error)
            self.assertTrue(any("֐" <= c <= "׿" for c in text), text)
            self.assertNotIn(KEY_E, text)
            self.assertNotIn("401", text)

    def test_only_a_401_invalid_key_marks_the_key_rejected(self):
        self.assertTrue(el.ElevenLabsError(401, "invalid_api_key").key_rejected)
        self.assertFalse(el.ElevenLabsError(401, "missing_permissions").key_rejected)
        self.assertFalse(el.ElevenLabsError(402, "quota_exceeded").key_rejected)

    def test_delete_voice_already_gone_is_done(self):
        HTTPX.next[:] = [FakeResponse(404)]
        self.assertTrue(el.delete_voice(KEY_E, "v1"))


# ─── 4. Voice recordings ─────────────────────────────────────────────────────

def _wav(seconds=2.0, rate=8000):
    data = b"\x00\x00" * int(rate * seconds)
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    return (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"data" + struct.pack("<I", len(data)) + data)


class VoiceRecordings(ToolCase):
    def test_format_from_bytes_not_name(self):
        self.assertEqual(vr.sniff(_wav()), "wav")
        self.assertEqual(vr.sniff(b"ID3\x04" + b"\x00" * 20), "mp3")
        self.assertEqual(vr.sniff(b"\xff\xfb\x90" + b"\x00" * 20), "mp3")
        self.assertEqual(vr.sniff(b"\x1aE\xdf\xa3" + b"\x00" * 20), "webm")
        self.assertEqual(vr.sniff(b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 20), "m4a")
        self.assertEqual(vr.sniff(b"%PDF-1.7" + b"\x00" * 20), "")
        with self.assertRaises(vr.VoiceError) as e:
            vr.upload(27, "song.mp3", b"%PDF-1.7 not audio")
        self.assertEqual(e.exception.code, "bad_format")

    def test_wav_duration_exact_and_stored_on_the_file(self):
        self.assertEqual(vr.wav_seconds(_wav(2.0)), 2.0)
        row = vr.upload(27, "hello.wav", _wav(3.0), duration_hint="999")
        self.assertEqual(row["duration_seconds"], 3.0)            # header beats the browser
        stored = DRIVE_FILES["client27/voice"][0]
        self.assertEqual(stored["appProperties"]["duration_seconds"], 3.0)

    def test_browser_duration_bounded(self):
        mp3 = b"ID3\x04" + b"\x00" * 50
        self.assertEqual(vr.upload(27, "a.mp3", mp3, "12.34")["duration_seconds"], 12.3)
        self.assertIsNone(vr.upload(27, "b.mp3", mp3, "-5")["duration_seconds"])
        self.assertIsNone(vr.upload(27, "c.mp3", mp3, "nan-ish")["duration_seconds"])

    def test_size_cap_and_empty(self):
        with self.assertRaises(vr.VoiceError) as e:
            vr.upload(27, "big.mp3", b"ID3" + b"\x00" * (vr.MAX_MB * 1024 * 1024))
        self.assertEqual(e.exception.code, "too_big")
        with self.assertRaises(vr.VoiceError) as e:
            vr.upload(27, "none.mp3", b"")
        self.assertEqual(e.exception.code, "empty")

    def test_stored_in_the_clients_own_voice_folder(self):
        row = vr.upload(27, "../../etc/passwd.mp3", b"ID3\x04" + b"\x00" * 50)
        self.assertEqual(DRIVE_CALLS[0][1], "client27/voice")
        self.assertNotIn("/", row["name"])
        self.assertTrue(row["name"].endswith(".mp3"))

    def test_delete_only_this_clients_ids(self):
        vr.upload(27, "mine.mp3", b"ID3\x04" + b"\x00" * 50)
        mine = vr.list_recordings(27)[0]["id"]
        result = vr.delete(27, [mine, "other-clients-file"])
        self.assertEqual(result["done"], [mine])
        self.assertEqual(result["failed"], [{"id": "other-clients-file", "code": "not_found"}])
        self.assertEqual([c for c in DRIVE_CALLS if c[0] == "trash"], [("trash", mine)])

    def test_agents_see_the_recordings_and_what_they_may_be_used_for(self):
        vr.upload(27, "mine.wav", _wav(2.0))
        view = vr.for_agent(27)
        self.assertEqual(view["count"], 1)
        self.assertEqual(view["total_seconds"], 2.0)
        self.assertEqual(view["usable_as"], ["audio_track"])        # no cloning consent
        CONSENTS.add((27, "voice"))
        self.assertEqual(vr.for_agent(27)["usable_as"], ["audio_track", "voice_clone_source"])


# ─── 5. Wiring: agent card, client_state, deletion, API, dashboard ──────────

class ConnectCreativeToolCard(unittest.TestCase):
    def setUp(self):
        import test_agent_actions as taa
        self.taa = taa
        self.case = taa.ActionTestCase("run")
        self.case.setUp()
        self.aa = taa.aa

    def tearDown(self):
        self.case.tearDown()

    def stored(self, tool):
        row = {"id": 1, "client_id": 27, "action": "connect_creative_tool", "params": {"tool": tool},
               "origin": "client_chat", "persona": "general", "approver": "client", "status": "proposed",
               "created_at": self.taa.datetime.now(self.taa.timezone.utc).isoformat()}
        self.case.db.tables.setdefault(self.aa.TABLE, []).append(row)
        return 1

    def test_opens_only_that_tools_card(self):
        result = self.aa.decide(self.stored("elevenlabs"), "client", 27, "approve")
        self.assertEqual(result["focus"], "toolCard-elevenlabs")
        self.assertNotIn("redirect", result)

    def test_unknown_tool_refused(self):
        self.assertEqual(self.aa.decide(self.stored("everything"), "client", 27, "approve")["code"],
                         "invalid_params")

    def test_offered_only_when_a_tool_is_missing(self):
        available = self.aa.ACTIONS["connect_creative_tool"]["available"]
        ok = {"creative_tools": {"tools": {"heygen": {"status": "connected"}}}}
        broken = {"creative_tools": {"tools": {"heygen": {"status": "key_rejected"}}}}
        self.assertFalse(available(ok, {}))
        self.assertTrue(available(broken, {}))
        self.assertFalse(available({}, {}))


class ClientStateSections(ToolCase):
    def test_creative_tools_section_has_no_key_and_no_last4(self):
        from core import client_state as cs
        CONSENTS.add((27, "likeness"))
        vt.connect(27, "heygen", KEY_H)
        section = cs._creative_tools(27)
        self.assertEqual(section["tools"]["heygen"]["status"], "connected")
        self.assertNotIn("last4", section["tools"]["heygen"])
        self.assertNotIn("AAAA", repr(section))
        self.assertEqual({p["id"] for p in section["video_providers"]}, {"heygen", "higgsfield"})

    def test_sections_registered_with_their_topics(self):
        from core import client_state as cs
        self.assertEqual(cs.SECTION_TOPIC["creative_tools"], "account")
        self.assertEqual(cs.SECTION_TOPIC["voice"], "voice")


class Wiring(unittest.TestCase):
    def test_creative_tools_record_is_cleared_and_guarded(self):
        self.assertIn('f"creative_tools:{int(i)}"', _src("core", "client_deletion.py"))
        self.assertIn('"creative_tools:"', _src("core", "deletion_guard.py"))
        self.assertIn('"creative_tools:"', _src("core", "admin_service.py"))

    def test_disconnect_groups_are_per_tool(self):
        src = _src("core", "api_server.py")
        self.assertIn('"heygen": ["heygen"],', src)
        self.assertIn('"elevenlabs": ["elevenlabs"],', src)

    def test_client_routes_are_session_gated(self):
        src = _src("core", "api_server.py")
        for route in ('"/api/client/tools"', '"/api/client/tools/{tool}/connect"', '"/api/client/tools/{tool}/test"',
                      '"/api/client/tools/{tool}/disconnect"', '"/api/client/tools/provider"',
                      '"/api/client/voice"', '"/api/client/voice/upload"', '"/api/client/voice/delete"'):
            block = src.split(route + ")", 1)[1].split("\n@app.", 1)[0]
            self.assertIn("_require_session(request)", block, route)

    def test_admin_view_may_read_but_not_write(self):
        from core.admin_view import READ_ENDPOINTS
        self.assertIn("client_tools", READ_ENDPOINTS)
        self.assertIn("client_voice_list", READ_ENDPOINTS)
        for write in ("client_tool_connect", "client_tool_disconnect", "client_voice_upload", "client_voice_delete"):
            self.assertNotIn(write, READ_ENDPOINTS)

    def test_help_entries_active_for_each_tool(self):
        from core import connection_help
        active = {c["id"] for c in connection_help.CONNECTIONS if c["active"]}
        self.assertTrue({"heygen", "elevenlabs", "higgsfield"} <= active)

    def test_no_google_scope_in_the_new_modules(self):
        for name in ("vendor_tools.py", "avatar_video.py", "voice_recordings.py", "elevenlabs_service.py"):
            self.assertNotIn("googleapis.com/auth", _src("core", name), name)

    def test_cost_rows_are_tagged_per_tool(self):
        self.assertIn('"tool": tool', _src("core", "cost_tracker.py"))
        self.assertIn("tool=tool", _src("agents", "avatar_agent.py"))


class DashboardCards(unittest.TestCase):
    def setUp(self):
        self.html = _src("dashboard", "client", "index.html")

    def test_one_card_per_tool_with_its_own_controls(self):
        for tool in ("heygen", "elevenlabs", "higgsfield"):
            for element in ("toolCard-", "toolStatus-", "toolKey-", "toolConnectBtn-", "toolDisc-", "toolForm-"):
                self.assertIn(f'id="{element}{tool}"', self.html, element + tool)
            self.assertIn(f'data-help-id="{tool}"', self.html)

    def test_no_shared_form_left(self):
        for old in ("avatarHeygenKey", "avatarElevenKey", "connectAvatar(", "/api/avatar/connect"):
            self.assertNotIn(old, self.html)

    def test_each_consent_on_its_own_tool(self):
        heygen = self.html.split('id="toolCard-heygen"', 1)[1].split('id="toolCard-elevenlabs"', 1)[0]
        eleven = self.html.split('id="toolCard-elevenlabs"', 1)[1].split('id="providerCard"', 1)[0]
        self.assertIn("avatar_consent_likeness", heygen)
        self.assertNotIn("tool_voice_consent", heygen)
        self.assertIn("tool_voice_consent", eleven)
        self.assertNotIn("avatar_consent_likeness", eleven)

    def test_voice_section(self):
        self.assertIn('id="voiceCard"', self.html)
        self.assertIn("/api/client/voice/upload", self.html)
        self.assertIn("xhr.upload.onprogress", self.html)
        self.assertIn("MediaRecorder", self.html)
        self.assertIn("delete_clone: true", self.html)


if __name__ == "__main__":
    unittest.main()
