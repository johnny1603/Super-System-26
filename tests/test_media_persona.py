"""The media specialist (ליאור) can answer TikTok, voice and presenter
questions — same persona, more reads (agents/support_agent._media_reads).

- No new persona: TikTok, voice recordings and the video presenter are media
  and content, so they belong to the existing media persona (one front door).
- Pure reads only: TikTok from STORED state (core/tiktok_grant + the
  client_accounts row), never a live TikTok call (the engagement getter
  rotates tokens as a side effect); drafts sent to the inbox from
  agent_actions' own history; creative tools + voice through client_state's
  cache (no second read in the same turn).
- Capability language: tool labels, never a vendor name.
- The persona proposes the real cards through the shared whitelist
  (agent_actions.available), and a stuck client gets request_connection_help.

Stdlib only. Run from the repo root:  python -I -B tests/test_media_persona.py -v
"""
import ast
import os
import re
import sys
import types
import unittest
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

VENDOR = re.compile(r"heygen|eleven\s?labs|higgsfield", re.IGNORECASE)


def _module(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    return m


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


ACCOUNTS = {}          # client_id -> set of active platforms
LIVE_TIKTOK_CALLS = []


def _boom(*a, **k):
    LIVE_TIKTOK_CALLS.append(a)
    raise AssertionError("a live TikTok call from the persona read path")


STUBS = {
    "httpx": _module("httpx"),
    "anthropic": _module("anthropic", Anthropic=lambda *a, **k: None),
    "supabase": _module("supabase", create_client=lambda *a, **k: None),
    "agents.client_agent": _module(
        "agents.client_agent",
        get_client=lambda cid: {"id": cid, "language": "he", "billing_exempt": False},
        get_activity=lambda *a, **k: [], get_communications=lambda *a, **k: [],
        log_activity=lambda *a, **k: None, _db=lambda: None,
        get_accounts=lambda cid: [{"platform": p, "status": "active", "has_secret": True}
                                  for p in sorted(ACCOUNTS.get(cid, ()))]),
    "agents.onboarding_agent": _module(
        "agents.onboarding_agent", LANGUAGE_RULE="\n\nLANGUAGE.",
        PRICING={"avatar": {"setup_first_avatar": 900, "monthly_tiers": [{"id": "basic"}]}}),
    "core.automation_service": _module("core.automation_service", VENDORS={}, POLICY_NOTE="",
                                       SIGNUP_RULE_HE=""),
    "core.agent_base": _module("core.agent_base", agent_alert=lambda *a, **k: None,
                               log_step=lambda *a, **k: None, timed_step=lambda a, s, fn: fn()),
    "agents.media_agent": _module("agents.media_agent", get_monthly_usage=lambda cid: {"images": 2},
                                  _subfolder=lambda *a: "x"),
    "agents.avatar_agent": _module("agents.avatar_agent", list_ready_avatars=lambda cid: [],
                                   has_consent=lambda cid, scope: False, current_voice_id=lambda cid: ""),
    # The live paths: any use from the read path fails the test
    "agents.tiktok_content_agent": _module("agents.tiktok_content_agent", get_engagement_summary=_boom,
                                           publish=_boom),
    "core.tiktok_service": _module("core.tiktok_service", ENGAGEMENT_READS_ENABLED=False,
                                   refresh_access_token=_boom, list_videos=_boom, query_videos=_boom),
}
_SAVED = {name: sys.modules.get(name) for name in list(STUBS) + ["agents.support_agent"]}
sys.modules.update(STUBS)
sys.modules.pop("agents.support_agent", None)

from agents import support_agent as sa     # noqa: E402
from core import agent_actions as aa      # noqa: E402
from core import client_state as cs       # noqa: E402
from core import tiktok_grant as tg       # noqa: E402
from test_agent_actions import FakeDB     # noqa: E402

TOOLS_SECTION = {
    "tools": {
        "heygen": {"label": "הדמות שבסרטונים — דמות מצולמת שלך", "status": "not_connected", "consent": False},
        "elevenlabs": {"label": "הקול שלך", "status": "key_rejected", "consent": False},
        "higgsfield": {"label": "הדמות שבסרטונים — דמות AI", "status": "connected_unverified",
                       "consent": None},
    },
    "video_providers": [
        {"id": "heygen", "tool": "heygen", "label_he": "דמות מצולמת שלך", "chosen": False, "ready": True,
         "connected": False},
        {"id": "higgsfield", "tool": "higgsfield", "label_he": "דמות AI", "chosen": True, "ready": False,
         "connected": True},
    ],
}
VOICE_SECTION = {"count": 2, "total_seconds": 41.0,
                 "recordings": [{"file_id": "a", "name": "one.mp3", "uploaded_at": "2026-10-20"},
                                {"file_id": "b", "name": "two.mp3", "uploaded_at": "2026-10-29"}],
                 "voice_clone_consent": False, "voice_clone_exists": False, "usable_as": ["audio_track"]}


class MediaReads(unittest.TestCase):
    def setUp(self):
        ACCOUNTS.clear()
        LIVE_TIKTOK_CALLS.clear()
        cs._cache.clear()
        self.db = FakeDB()
        self.fetches = []
        self.saved = (cs._creative_tools, cs._voice, tg.record, aa._db)
        cs._creative_tools = lambda cid: self.fetches.append("creative_tools") or dict(TOOLS_SECTION)
        cs._voice = lambda cid: self.fetches.append("voice") or dict(VOICE_SECTION)
        self.grant = {}
        tg.record = lambda cid: dict(self.grant)
        aa._db = lambda: self.db
        os.environ.pop("HIGGSFIELD_AVATAR_VIDEO_ENABLED", None)

    def tearDown(self):
        cs._creative_tools, cs._voice, tg.record, aa._db = self.saved
        cs._cache.clear()

    # ── TikTok, from stored state ──

    def test_tiktok_connected_with_upload_scope(self):
        ACCOUNTS[40] = {"tiktok"}
        self.grant = {"state": "ok", "scopes": ["user.info.basic", "video.upload"]}
        tiktok = sa._media_reads(40)["tiktok"]
        self.assertEqual(tiktok, {"connected": True, "engagement_counts": "not_available_yet", "grant": "ok",
                                  "scopes": ["user.info.basic", "video.upload"], "needs_reconnect": False,
                                  "can_send_to_inbox": True})
        self.assertEqual(LIVE_TIKTOK_CALLS, [])

    def test_tiktok_grant_without_upload_scope_needs_reconnect(self):
        ACCOUNTS[40] = {"tiktok"}
        self.grant = {"state": "missing_upload", "scopes": ["user.info.basic", "video.publish"]}
        tiktok = sa._media_reads(40)["tiktok"]
        self.assertTrue(tiktok["needs_reconnect"])
        self.assertFalse(tiktok["can_send_to_inbox"])

    def test_tiktok_not_connected(self):
        tiktok = sa._media_reads(40)["tiktok"]
        self.assertEqual(tiktok, {"connected": False, "engagement_counts": "not_available_yet"})

    def test_engagement_is_a_known_state_never_sync_failed(self):
        ACCOUNTS[40] = {"tiktok"}
        self.grant = {"state": "unknown"}
        data = repr(sa._media_reads(40))
        self.assertIn("not_available_yet", data)
        self.assertNotIn("sync_failed", data)

    def test_no_live_tiktok_call_in_the_read_path(self):
        source = _src("agents", "support_agent.py")
        reads = source.split("def _tiktok_view", 1)[1].split("\nPERSONAS = {", 1)[0]
        for live in ("get_engagement_summary", "refresh_access_token", "list_videos", "query_videos", "publish("):
            self.assertNotIn(live, reads, live)

    # ── drafts sent to the TikTok inbox ──

    def test_drafts_sent_come_from_agent_actions_history(self):
        self.db.tables[aa.TABLE] = [{"id": 1, "client_id": 40, "action": "send_tiktok_to_inbox",
                                     "status": "done", "outcome": "done", "params": {"drive_file_id": "x"},
                                     "created_at": datetime.now(timezone.utc).isoformat(),
                                     "finished_at": "2026-10-30T09:00:00+00:00"}]
        drafts = sa._media_reads(40)["tiktok_inbox_drafts"]
        self.assertEqual(drafts["sent_to_inbox"], [{"file": "", "sent_at": "2026-10-30"}])
        self.assertIn("פרסם", drafts["client_must_do"])

    def test_unreadable_history_is_left_out_not_shown_as_an_error(self):
        self.db.fail = True
        data = sa._media_reads(40)
        self.assertNotIn("tiktok_inbox_drafts", data)
        self.assertNotIn("error", repr(data).lower())

    # ── voice, tools and presenter readiness ──

    def test_voice_recordings_count_and_newest(self):
        voice = sa._media_reads(40)["voice_recordings"]
        self.assertEqual((voice["count"], voice["newest"]), (2, "2026-10-29"))
        self.assertFalse(voice["voice_clone_consent"])
        self.assertEqual(voice["usable_as"], ["audio_track"])

    def test_tools_in_capability_language(self):
        tools = sa._media_reads(40)["video_and_voice_tools"]
        self.assertEqual({t["tool"]: t["status"] for t in tools},
                         {"heygen": "not_connected", "elevenlabs": "key_rejected",
                          "higgsfield": "connected_unverified"})
        for tool in tools:
            self.assertIsNone(VENDOR.search(tool["label"]), tool)

    def test_presenter_that_cannot_run_yet_is_said_plainly(self):
        readiness = sa._media_reads(40)["avatar_readiness"]
        self.assertFalse(readiness["can_make_presenter_video"])
        self.assertIn("עוד לא זמינים", readiness["not_available_line"])
        self.assertIsNone(VENDOR.search(readiness["not_available_line"]))
        self.assertEqual([p["label"] for p in readiness["presenters"]], ["דמות מצולמת שלך", "דמות AI"])

    def test_creative_tools_and_voice_share_the_snapshot_cache(self):
        sa._media_reads(40)
        self.assertEqual(sorted(self.fetches), ["creative_tools", "voice"])
        # The snapshot's own read of the same sections in this turn is a cache hit
        cs._section(40, "voice", lambda: cs._voice(40), False)
        cs._section(40, "creative_tools", lambda: cs._creative_tools(40), False)
        self.assertEqual(sorted(self.fetches), ["creative_tools", "voice"])

    def test_a_failed_voice_read_is_left_out(self):
        cs._voice = lambda cid: (_ for _ in ()).throw(RuntimeError("drive down"))
        cs._warn = lambda *a, **k: None
        self.assertNotIn("voice_recordings", sa._media_reads(40))


class PersonaShape(unittest.TestCase):
    def test_no_new_persona(self):
        self.assertEqual(set(sa.PERSONAS), {"google", "meta", "website", "media"})
        self.assertIn("TikTok", sa.PERSONAS["media"]["domain"])

    def test_reads_are_read_only_by_construction(self):
        source = _src("agents", "support_agent.py")
        reads = source.split("def _tiktok_view", 1)[1].split("\nPERSONAS = {", 1)[0]
        for write in ("propose(", "decide(", "upsert", ".insert(", ".table(", ".delete(", "connect(",
                      "make_video", "clone_voice(", "text_to_speech", "record_scopes", "record_dead", "_write("):
            self.assertNotIn(write, reads, write)

    def test_data_notes_explain_every_state(self):
        notes = sa.PERSONAS["media"]["data_notes"]
        for needed in ("client_must_do", "not_available_yet", "needs_reconnect", "voice_clone_consent",
                       "soundtrack", "consent", "connect_tiktok", "connect_creative_tool",
                       "send_tiktok_to_inbox", "clone_voice", "make_presenter_video",
                       "request_connection_help", "never send them to a website"):
            self.assertIn(needed, notes, needed)
        self.assertIsNone(VENDOR.search(notes))
        # data_notes is a format VALUE, but keep it brace-free like the rules
        self.assertNotIn("{", notes)


class CardsThroughTheSharedWhitelist(unittest.TestCase):
    """The media persona gets the same available_actions every chat gets —
    filtered by real availability, never by persona."""

    STATE = {"channels": {"by_channel": {"tiktok": {"status": "not_connected"}}},
             "creative_tools": {"tools": {"elevenlabs": {"status": "key_rejected"}}},
             "media_folder": {"videos": [{"drive_file_id": "1AbCdEfGhIjKlMnOp", "tiktok_uploadable": True}]}}

    def test_relevant_cards_offered_when_they_fix_something(self):
        names = {a["name"] for a in aa.available(40, "client_chat", self.STATE,
                                                 client={"id": 40, "billing_exempt": False})}
        self.assertTrue({"connect_tiktok", "connect_creative_tool", "send_tiktok_to_inbox",
                         "request_connection_help"} <= names, names)
        # internal_only until the live test: never offered to a paying client
        self.assertFalse({"clone_voice", "make_presenter_video"} & names)

    def test_nothing_to_fix_nothing_offered(self):
        healthy = {"channels": {"by_channel": {"tiktok": {"status": "active"}}},
                   "creative_tools": {"tools": {"elevenlabs": {"status": "connected"}}},
                   "media_folder": {"videos": []}}
        names = {a["name"] for a in aa.available(40, "client_chat", healthy,
                                                 client={"id": 40, "billing_exempt": False})}
        self.assertFalse({"connect_tiktok", "connect_creative_tool", "send_tiktok_to_inbox"} & names)

    def test_the_persona_path_uses_the_shared_whitelist(self):
        source = _src("agents", "support_agent.py")
        persona = source.split("def _answer_persona_question", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_account_context(client_id, client, payload)", persona)
        self.assertIn("_route_proposal(", persona)


class FeatureCatalog(unittest.TestCase):
    def test_tiktok_is_described_as_inbox_drafts_and_self_serve(self):
        from core import feature_catalog as fc
        tiktok = fc.feature("tiktok")
        self.assertEqual(tiktok["access"], "self_serve")
        self.assertEqual(tiktok["persona"], "media")
        self.assertIn("טיוטה", tiktok["what_he"])
        self.assertIn("לא מפרסמים בשמכם", tiktok["what_he"])
        self.assertIn("צפיות", tiktok["what_he"])                 # counts not read yet, said plainly

    def test_media_capabilities_belong_to_the_media_persona(self):
        from core import feature_catalog as fc
        for key in ("tiktok", "tiktok_inbox_from_media", "voice_recordings", "avatar"):
            self.assertEqual(fc.feature(key)["persona"], "media", key)
            self.assertEqual(fc.feature(key)["access"], "self_serve", key)
        self.assertIn("ליאור על תוכן ומדיה (כולל טיקטוק", fc.feature("team_chat")["what_he"])
        self.assertIn("נתקעתם", fc.feature("connection_help")["what_he"])

    def test_interview_renders_the_catalog_not_its_own_copy(self):
        source = _src("agents", "interview_agent.py")
        self.assertIn("catalog_for_prompt", source)
        self.assertNotIn("טיקטוק", source)


def tearDownModule():
    for name, module in _SAVED.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


if __name__ == "__main__":
    unittest.main()
