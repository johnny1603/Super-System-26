"""The presenter video after HeyGen was retired (2026-10-31): Higgsfield is the
ONE presenter path (core/avatar_video + core/media_gen_service).

1. Guard: no client-facing path reaches core/heygen_service or the retired
   HeyGenProvider again — no import, no provider entry, no connect, no help
   entry, no card, no catalog connection.
2. One model id, and the payload carries the character as start_image and
   the voice as audio_references.
3. Length honesty: at most MAX_CLIP_SECONDS, refused with an offer to split,
   never cut; an unknown length is refused too; the card states the length.
4. Privacy + consent: the character and the audio are public only while the
   render runs, never on a refusal; likeness consent for the client's own
   photo, none for an AI-generated character.
5. Gate: internal (billing_exempt) clients only until the live run;
   PRESENTER_VIDEO_ALL_CLIENTS opens it.

Stdlib only — every vendor, Drive and the client store are stubs.
Run from the tests folder:  python -I -B test_presenter_higgsfield.py -v
"""
import os
import re
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# ─── Stubs ───────────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status_code=200, body=None):
        self.status_code, self._body, self.text, self.content = status_code, body or {}, "", b""

    def json(self):
        return self._body


class RecordingHttpx(types.ModuleType):
    def __init__(self):
        super().__init__("httpx")
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return FakeResponse(200, {"id": "job1"})

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return FakeResponse(200, {"status": "completed", "results": [{"url": "https://hf/out.mp4"}],
                                  "credits": 7})

    class Response:
        pass


HTTPX = RecordingHttpx()
sys.modules["httpx"] = HTTPX
supabase_stub = types.ModuleType("supabase")
supabase_stub.create_client = lambda *a, **k: None
sys.modules.setdefault("supabase", supabase_stub)

ACCOUNTS = {}          # (client, platform) -> key
INTERNAL = set()       # billing_exempt client ids
client_agent = types.ModuleType("agents.client_agent")
client_agent.get_accounts = lambda cid: [
    {"platform": p, "status": "active", "has_secret": True} for (c, p) in ACCOUNTS if c == cid]
client_agent.get_client = lambda cid: {"id": cid, "billing_exempt": cid in INTERNAL}
client_agent.upsert_account = lambda cid, platform, account_id="", secret="", status="active": (
    ACCOUNTS.__setitem__((cid, platform), secret))
client_agent.remove_accounts = lambda cid, platforms=None: len(
    [p for p in (platforms or []) if ACCOUNTS.pop((cid, p), None) is not None])
client_agent._db = lambda: None
sys.modules["agents.client_agent"] = client_agent

CONSENTS = set()
CHARACTERS = []        # what list_ready_avatars returns
avatar_stub = types.ModuleType("agents.avatar_agent")
avatar_stub.has_consent = lambda cid, scope: (cid, scope) in CONSENTS
avatar_stub.record_consent = lambda cid, scope: CONSENTS.add((cid, scope)) or {"success": True}
avatar_stub.current_voice_id = lambda cid: "clone1"
avatar_stub.list_ready_avatars = lambda cid: [dict(c) for c in CHARACTERS]
avatar_stub._source_files = lambda cid: [{"id": c["avatar_id"]} for c in CHARACTERS]
sys.modules["agents.avatar_agent"] = avatar_stub

vault = types.ModuleType("core.secrets_vault")
vault.use_vendor_key = lambda cid, service, job="": ACCOUNTS.get((cid, service), "")
sys.modules["core.secrets_vault"] = vault

DRIVE_FILES = {}
SHARE_LOG = []         # ("open"|"close", file_id) in order
PUBLIC = set()
drive_stub = types.ModuleType("core.drive_service")
drive_stub.list_files = lambda folder, fields="": [dict(f) for f in DRIVE_FILES.get(folder, [])]
drive_stub.upload_bytes = lambda folder, name, content, mime, app_properties=None: {"id": "tts1"}


def _make_public(fid):
    SHARE_LOG.append(("open", fid))
    PUBLIC.add(fid)
    return f"https://drive.example/{fid}"


def _revoke(fid):
    SHARE_LOG.append(("close", fid))
    PUBLIC.discard(fid)
    return True


drive_stub.make_file_public = _make_public
drive_stub.revoke_public = _revoke
sys.modules["core.drive_service"] = drive_stub

media_stub = types.ModuleType("agents.media_agent")
media_stub._subfolder = lambda cid, *path: f"client{cid}/" + "/".join(path)
sys.modules["agents.media_agent"] = media_stub

from core import avatar_video as av            # noqa: E402
from core import connection_help as ch         # noqa: E402
from core import media_gen_service as mg       # noqa: E402
from core import vendor_tools as vt            # noqa: E402

KEY_HF = "hf-key-2222-CCCC"
RENDERS = []
_real_generate = mg.generate_talking_character


def _fake_generate(key, image_url, audio_url, seconds=0, aspect_ratio="9:16"):
    # The character and the voice must be public WHILE the model reads them
    RENDERS.append({"image_url": image_url, "audio_url": audio_url, "seconds": seconds,
                    "public_during": set(PUBLIC)})
    return {"url": "https://hf/out.mp4", "job_id": "job1", "credits": 7, "duration": seconds,
            "model": mg.PRESENTER_MODEL}


class Case(unittest.TestCase):
    def setUp(self):
        for store in (ACCOUNTS, CONSENTS, DRIVE_FILES, PUBLIC, SHARE_LOG, RENDERS, HTTPX.calls):
            store.clear()
        INTERNAL.clear()
        INTERNAL.add(27)
        CHARACTERS[:] = [{"avatar_id": "char1", "avatar_name": "Me", "kind": "own_photo"}]
        ACCOUNTS[(27, "higgsfield")] = KEY_HF
        CONSENTS.add((27, "likeness"))
        os.environ.pop(av.OPEN_TO_ALL_ENV, None)
        mg.generate_talking_character = _fake_generate
        self.saved_vt = (vt._record, vt._save, vt._log)
        self.records = {}
        vt._record = lambda cid: dict(self.records.get(cid, {}))
        vt._save = lambda cid, rec: self.records.__setitem__(cid, dict(rec))
        vt._log = lambda *a, **k: None

    def tearDown(self):
        mg.generate_talking_character = _real_generate
        vt._record, vt._save, vt._log = self.saved_vt
        os.environ.pop(av.OPEN_TO_ALL_ENV, None)

    def recording(self, seconds):
        props = {"uploaded_at": "2026-10-31T10:00:00+00:00"}
        if seconds is not None:
            props["duration_seconds"] = seconds
        DRIVE_FILES["client27/voice"] = [{"id": "rec1", "name": "me.mp3", "mimeType": "audio/mpeg",
                                          "size": 10, "appProperties": props}]


# ─── 1. Guard: HeyGen is unreachable from every client-facing path ───────────

IMPORT_HEYGEN = re.compile(r"^\s*(from\s+core\s+import\s+[^\n]*\bheygen_service\b"
                           r"|from\s+core\.heygen_service\s+import|import\s+core\.heygen_service)", re.M)


class HeyGenUnreachable(Case):
    def test_nothing_imports_heygen_service(self):
        hits = []
        for folder in ("core", "agents"):
            for name in os.listdir(os.path.join(ROOT, folder)):
                if name.endswith(".py") and name != "heygen_service.py":
                    if IMPORT_HEYGEN.search(_src(folder, name)):
                        hits.append(f"{folder}/{name}")
        self.assertEqual(hits, [], "a module imports the retired core/heygen_service again")
        self.assertIn("RETIRED", _src("core", "heygen_service.py")[:400])

    def test_no_heygen_provider(self):
        self.assertEqual(list(av.PROVIDERS), ["higgsfield"])
        self.assertEqual(av.DEFAULT_PROVIDER, "higgsfield")
        self.assertFalse(av.RETIRED_PROVIDERS["heygen"].enabled(27))
        with self.assertRaises(RuntimeError):
            av.RETIRED_PROVIDERS["heygen"].render(27, "k", "u", 5, {})

    def test_a_stored_heygen_choice_still_renders_on_higgsfield(self):
        self.recording(6.0)
        result = av.make_video(27, "", {"kind": "recording", "file_id": "rec1"}, provider="heygen")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["provider"], "higgsfield")
        self.assertEqual(len(RENDERS), 1)

    def test_heygen_cannot_be_connected_or_chosen(self):
        with self.assertRaises(vt.ToolError) as e:
            vt.connect(27, "heygen", "hg-key", consent=True)
        self.assertEqual(e.exception.code, "unknown_tool")
        with self.assertRaises(vt.ToolError):
            vt.set_default_provider(27, "heygen")
        self.assertNotIn("heygen", vt.TOOLS)
        self.assertNotIn("heygen", vt.statuses(27))

    def test_no_help_card_or_catalog_entry(self):
        entry = next(c for c in ch.CONNECTIONS if c["id"] == "heygen")
        self.assertFalse(entry["active"])
        self.assertTrue(entry.get("retired"))
        self.assertEqual(ch.guide_view("heygen", "he"), {})
        from core import feature_catalog
        for feature in feature_catalog.FEATURES:
            self.assertNotIn("heygen", feature.get("connections") or [], feature.get("id"))

    def test_no_client_page_or_agent_prompt_names_it(self):
        for page in (("dashboard", "client", "index.html"), ("dashboard", "profile", "index.html")):
            self.assertIsNone(re.search("heygen", _src(*page), re.I), page)
        for name in ("support_agent.py", "qa_agent_content.py", "onboarding_agent.py"):
            src = _src("agents", name)
            self.assertNotIn('"heygen"', src.lower().replace("'", '"'), name)

    def test_chat_cards_offer_only_live_tools(self):
        src = _src("core", "agent_actions.py")
        spec = src.split('"connect_creative_tool": {', 1)[1].split("\n    },", 1)[0]
        self.assertNotIn("heygen", spec.lower())

    def test_footage_create_route_is_retired(self):
        src = _src("core", "api_server.py")
        block = src.split("def client_avatar_footage_create", 1)[1].split("\n@app.", 1)[0]
        self.assertIn('_footage_refusal("retired", status=410)', block)
        self.assertNotIn("avatar_footage.create", block)

    def test_readiness_scan_is_a_no_op(self):
        src = _src("agents", "avatar_agent.py")
        scan = src.split("def run_readiness_scan", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('"retired": True', scan)


# ─── 2. One model, the right inputs ──────────────────────────────────────────

class Model(Case):
    def test_model_named_in_one_place(self):
        self.assertEqual(mg.PRESENTER_MODEL, "wan2_7")
        offenders = []
        for folder in ("core", "agents"):
            for name in os.listdir(os.path.join(ROOT, folder)):
                if name.endswith(".py") and name != "media_gen_service.py" and "wan2_7" in _src(folder, name):
                    offenders.append(name)
        self.assertEqual(offenders, [])

    def test_payload_has_start_image_and_audio_references(self):
        saved = (mg._count, mg.time.sleep)
        mg._count = lambda *a: None
        mg.time.sleep = lambda s: None
        try:
            out = _real_generate(KEY_HF, "https://img", "https://aud", seconds=40)
        finally:
            mg._count, mg.time.sleep = saved
        method, url, kwargs = HTTPX.calls[0]
        payload = kwargs["json"]
        self.assertEqual(method, "POST")
        self.assertEqual(payload["model"], mg.PRESENTER_MODEL)
        self.assertEqual(payload["medias"], [{"role": "start_image", "url": "https://img"},
                                             {"role": "audio_references", "url": "https://aud"}])
        self.assertEqual(payload["duration"], mg.PRESENTER_MAX_SECONDS)   # the model's ceiling
        self.assertEqual(out["url"], "https://hf/out.mp4")
        self.assertEqual(av.MAX_CLIP_SECONDS, mg.PRESENTER_MAX_SECONDS)


# ─── 3. Length honesty ───────────────────────────────────────────────────────

LONG = " ".join(["זה משפט קצר על העסק שלנו ועל מה שאנחנו עושים כל יום."] * 8)


class Length(Case):
    def test_long_script_refused_with_a_split_offer_nothing_opened(self):
        ACCOUNTS[(27, "elevenlabs")] = "el"
        CONSENTS.add((27, "voice"))
        result = av.make_video(27, LONG, {"kind": "clone"})
        self.assertEqual(result["code"], "too_long")
        self.assertGreaterEqual(len(result["parts"]), 2)
        self.assertIn("לחלק", result["message_he"])
        self.assertEqual((SHARE_LOG, RENDERS), ([], []))
        for part in result["parts"]:
            self.assertTrue(av.fits(av.estimate_seconds(part)), part)
        # Whole text kept: the parts are the script, not a cut of it
        self.assertEqual(" ".join(result["parts"]).split(), LONG.split())

    def test_long_recording_refused_not_cut(self):
        self.recording(42.0)
        result = av.make_video(27, "", {"kind": "recording", "file_id": "rec1"})
        self.assertEqual(result["code"], "too_long")
        self.assertIn("42", result["message_he"])
        self.assertEqual((SHARE_LOG, RENDERS), ([], []))

    def test_unknown_length_refused(self):
        self.recording(None)
        result = av.make_video(27, "", {"kind": "recording", "file_id": "rec1"})
        self.assertEqual(result["code"], "length_unknown")
        self.assertEqual(RENDERS, [])

    def test_within_the_grace_still_fits(self):
        self.assertTrue(av.fits(av.MAX_CLIP_SECONDS + av.LENGTH_GRACE_SECONDS))
        self.assertFalse(av.fits(av.MAX_CLIP_SECONDS + av.LENGTH_GRACE_SECONDS + 0.1))

    def test_card_states_the_length_and_refuses_a_long_script(self):
        from core import agent_actions as aa
        self.assertIn("{length}", aa.ACTIONS["make_presenter_video"]["what"]["he"])
        long_card = aa._prepare_presenter_video(27, {"script": LONG, "voice": "clone"}, {"language": "he"}, [])
        self.assertIn("refuse", long_card)
        self.assertIn("לחלק", long_card["refuse"])
        short = {"script": "שלום, אני דנה מהמאפייה.", "voice": "clone"}
        self.assertEqual(aa._prepare_presenter_video(27, short, {"language": "he"}, []), {"params": short})
        saved = aa._lang
        aa._lang = lambda cid: "he"
        try:
            display = aa._presenter_display(27, short)
        finally:
            aa._lang = saved
        self.assertIn("אורך הסרטון", display["length"])
        self.assertIn(str(av.MAX_CLIP_SECONDS), display["length"])


# ─── 4. Privacy and consent ──────────────────────────────────────────────────

class PrivacyAndConsent(Case):
    def test_public_only_while_rendering_then_closed(self):
        self.recording(8.0)
        result = av.make_video(27, "", {"kind": "recording", "file_id": "rec1"})
        self.assertTrue(result["success"], result)
        self.assertEqual(RENDERS[0]["public_during"], {"char1", "rec1"})
        self.assertEqual(RENDERS[0]["image_url"], "https://drive.example/char1")
        self.assertEqual(PUBLIC, set())
        self.assertEqual({f for op, f in SHARE_LOG if op == "close"}, {"char1", "rec1"})

    def test_own_photo_needs_likeness_consent_and_nothing_is_shared(self):
        CONSENTS.clear()
        self.recording(8.0)
        result = av.make_video(27, "", {"kind": "recording", "file_id": "rec1"})
        self.assertEqual(result["code"], "no_likeness_consent")
        self.assertEqual(result["buttons"][0]["focus"], "toolCard-higgsfield")
        self.assertIsNone(re.search("higgsfield|heygen", result["message_he"], re.I))
        self.assertEqual((SHARE_LOG, RENDERS), ([], []))

    def test_generated_character_needs_no_likeness_consent(self):
        CONSENTS.clear()
        CHARACTERS[:] = [{"avatar_id": "char2", "avatar_name": "AI", "kind": "generated"}]
        self.recording(8.0)
        result = av.make_video(27, "", {"kind": "recording", "file_id": "rec1"})
        self.assertTrue(result["success"], result)
        self.assertEqual(result["character_kind"], "generated")

    def test_no_character_refused_before_sharing(self):
        CHARACTERS[:] = []
        self.recording(8.0)
        self.assertEqual(av.make_video(27, "", {"kind": "recording", "file_id": "rec1"})["code"], "no_character")
        self.assertEqual(SHARE_LOG, [])

    def test_consent_wording_names_no_vendor(self):
        src = _src("agents", "avatar_agent.py")
        statements = src.split("CONSENT_STATEMENTS_HE", 1)[1].split("}", 1)[0]
        self.assertIsNone(re.search("heygen|higgsfield|eleven", statements, re.I))
        self.assertIn('CONSENT_VERSION = "2026-10-31.v4"', src)


# ─── 5. Gate ─────────────────────────────────────────────────────────────────

class Gate(Case):
    def test_internal_yes_paying_no_env_opens(self):
        self.assertTrue(av.presenter_allowed(27))
        self.assertFalse(av.presenter_allowed(40))
        os.environ[av.OPEN_TO_ALL_ENV] = "1"
        self.assertTrue(av.presenter_allowed(40))

    def test_paying_client_gets_the_plain_line_and_no_vendor_call(self):
        ACCOUNTS[(40, "higgsfield")] = "k40"
        result = av.make_video(40, "", {"kind": "recording", "file_id": "rec1"})
        self.assertEqual(result["code"], "provider_not_ready")
        self.assertEqual(result["message_he"], av.not_ready_line())
        self.assertEqual((SHARE_LOG, RENDERS), ([], []))

    def test_unknown_client_fails_closed(self):
        saved = client_agent.get_client
        client_agent.get_client = lambda cid: (_ for _ in ()).throw(RuntimeError("db down"))
        try:
            self.assertFalse(av.presenter_allowed(27))
        finally:
            client_agent.get_client = saved

    def test_chat_cards_internal_only_by_default(self):
        from core import agent_actions as aa
        self.assertTrue(aa._presenter_internal_only())
        for name in ("create_presenter_from_photos", "make_presenter_video"):
            self.assertTrue(aa.ACTIONS[name]["internal_only"], name)
        self.assertNotIn("HIGGSFIELD_AVATAR_VIDEO_ENABLED", _src("core", "avatar_video.py"))


if __name__ == "__main__":
    unittest.main()
