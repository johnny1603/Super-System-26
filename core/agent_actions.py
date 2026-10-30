"""agent_actions — what an agent may SET IN MOTION, and only through a human click.

The shape (propose → approve → execute):

1. A chat model (client concierge/persona, or a Control Room persona) may
   return `proposed_action` {name, params, summary}. It runs nothing.
2. `propose()` checks it in CODE against the whitelist below: the name, who
   may propose it from this chat, the internal-client flag, whether it makes
   sense for this client right now (client_state), and every param against
   its validator. Only then is a row stored (table `agent_actions`,
   migrations/2026-10-21-agent-actions.sql) and a confirmation card returned.
   The card's text is OURS (registry), never the model's summary.
3. Only a human click calls `decide()`. The browser sends the proposal id and
   approve/cancel — never params, so there is nothing to tamper with. The
   server re-checks everything from the stored row: caller vs approver,
   ownership, expiry, internal-client flag, params, then claims the row with a
   compare-and-set (status open → running) so a double click runs it once.
4. The action runs through the SAME function its fallback button uses
   (website_agent.configure_lead_conversion, core/verification_demo,
   engagement_agent.decide_suggestion, the OAuth start routes); the action's
   own guards (container match, dead token, already live, PAUSED, Private)
   stay where they are and refuse here exactly as they do for the button.
5. The outcome is written to the audit row (a short code — no Google data, see
   the migration) and posted into the chat thread it came from.

Who approves: "client" actions change only the client's OWN accounts, in a
way the card spells out, and the client approves them with one click (a
reconnect, their own pending suggestion, a PRIVATE YouTube upload, a TikTok
draft, the lead-tracking tag in their own Tag Manager, a PAUSED test
campaign on an internal test client). "admin" actions are approved in the
Control Room: proposed from the client chat they become a staff request
(status awaiting_staff, the team is alerted, the client is told plainly
what happens next). "admin_proposable": a client action the Control Room
chat may also propose — staff then approve it there.
"""
import json
import re
import time
from datetime import datetime, timedelta, timezone
from core import drive_storage

SERVICE_NAME = "agent_actions"
TABLE = "agent_actions"
PROPOSAL_TTL_HOURS = 24
OPEN_STATUSES = ("proposed", "awaiting_staff")
LANGS = ("he", "en", "fr", "ar", "ru")


class ActionFailed(Exception):
    """An action's own refusal or failure. `code` goes to the audit row;
    the message only to the admin/log (it may carry platform detail)."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


# ─── Seams (patched by tests; everything external goes through these) ────────

def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _client(client_id: int) -> dict:
    from agents.client_agent import get_client
    return get_client(client_id) or {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _log(message: str, **fields):
    from core.redaction import redact
    print(json.dumps({"severity": fields.pop("severity", "INFO"), "message": redact(message),
                      "service": SERVICE_NAME, **fields}, ensure_ascii=False, default=str), flush=True)


def _alert(issue: str):
    from core.agent_base import agent_alert
    agent_alert(SERVICE_NAME, [issue])


def _post_client_message(client_id: int, persona: str, text: str):
    from agents.client_agent import log_communication
    from agents.support_agent import persona_channel
    log_communication(client_id, "outbound", persona_channel(persona), text)


def _post_admin_message(client_id: int, persona: str, text: str):
    from core import control_room
    control_room._store(client_id, persona, "agent", text)


def _forget_state(client_id: int):
    """After an action changed something, the next turn reads fresh (e.g.
    today's upload count)."""
    try:
        from core import client_state
        client_state.forget(client_id)
    except Exception:
        pass


# ─── Param validators (value, client_id) -> normalized value | ValueError ────

_GA4_RE = re.compile(r"^G-[A-Z0-9]{4,15}$")


def _v_ga4(value, client_id: int):
    value = str(value or "").strip().upper()
    if value and not _GA4_RE.match(value):
        raise ValueError("ga4_measurement_id must look like G-XXXXXXX")
    return value


def _v_pending_suggestion(value, client_id: int):
    try:
        sid = int(value)
    except (TypeError, ValueError):
        raise ValueError("suggestion_id must be a number")
    from agents.engagement_agent import get_suggestions
    if sid not in {s.get("id") for s in get_suggestions(client_id, status="pending", limit=50)}:
        raise ValueError("not one of this client's pending suggestions")
    return sid


_DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,128}$")
MEDIA_META_TTL = 600
_media_meta = {}   # (client_id, file_id) -> (time, {name, mimeType, size}) — display + re-checks


def _media_upload_max_mb() -> int:
    from core.video_limits import VIDEO_UPLOAD_MAX_MB   # one cap for every Drive→YouTube upload
    return VIDEO_UPLOAD_MAX_MB


@drive_storage.scoped
def _v_media_video(value, client_id: int):
    """A video in THIS client's Drive media folder, proven by Drive's own
    parents chain (drive_mirror's walk, fresh for files), never by what the
    model or the browser said; a video MIME type, not trashed, within the
    upload size cap (the upload is read into memory)."""
    file_id = str(value or "").strip()
    if not _DRIVE_ID_RE.match(file_id):
        raise ValueError("drive_file_id is not a Drive id")
    from core import drive_mirror, drive_service
    root = drive_mirror.client_root_id(client_id)
    if not root or file_id == root:
        raise ValueError("no media folder / not a file")
    try:
        drive_mirror._assert_in_client_tree(client_id, root, file_id)
    except drive_mirror.NotInClientFolder:
        raise ValueError("drive_file_id is not in this client's media folder")
    except Exception as e:
        raise ValueError(f"could not verify the file ({type(e).__name__})")
    try:
        meta = drive_service.get_meta(file_id, "id,name,mimeType,size,trashed")
    except Exception as e:
        raise ValueError(f"could not read the file ({type(e).__name__})")
    if meta.get("trashed"):
        raise ValueError("the file is in the trash")
    if not str(meta.get("mimeType") or "").startswith("video/"):
        raise ValueError("not a video")
    from core.video_limits import fits
    if not fits(meta.get("size")):
        raise ValueError(f"video must be up to {_media_upload_max_mb()} MB")
    _media_meta[(client_id, file_id)] = (time.time(), meta)
    return file_id


def _v_tiktok_video(value, client_id: int):
    """_v_media_video's proof (the client's own folder, a video, not trashed)
    plus TikTok's own cap: the inbox upload is ONE chunk (core/video_limits)."""
    file_id = _v_media_video(value, client_id)
    from core.video_limits import TIKTOK_INBOX_MAX_MB, fits_tiktok
    hit = _media_meta.get((client_id, file_id))
    if not hit or not fits_tiktok((hit[1] or {}).get("size")):
        raise ValueError(f"video must be up to {TIKTOK_INBOX_MAX_MB} MB for TikTok")
    return file_id


_TITLE_DROP = re.compile(r"[\u0000-\u001f\u007f‎‏‪-‮⁦-⁩<>]")
YOUTUBE_TITLE_MAX = 100   # YouTube's own limit; it also rejects < and >


def _v_title(value, client_id: int):
    title = " ".join(_TITLE_DROP.sub(" ", str(value or "")).split())[:YOUTUBE_TITLE_MAX].strip()
    if len(title) < 2:
        raise ValueError("title too short")
    return title


@drive_storage.scoped
def _media_name(client_id: int, file_id: str) -> str:
    hit = _media_meta.get((client_id, file_id))
    if hit and hit[0] > time.time() - MEDIA_META_TTL:
        return hit[1].get("name", "")
    try:
        from core import drive_service
        meta = drive_service.get_meta(file_id, "id,name,mimeType,size,trashed")
        _media_meta[(client_id, file_id)] = (time.time(), meta)
        return meta.get("name", "")
    except Exception:
        return ""


# ─── Runners: the SAME functions the fallback buttons call ───────────────────

# configure_lead_conversion's reason → the refusal the chat explains (the rest
# — gtm_error, gtm_api_disabled, gtm_compiler_error — are ours: _FAILED + alert)
_GTM_REFUSAL_CODES = {"gtm_not_connected": "gtm_reconnect", "gtm_reconnect_needed": "gtm_reconnect",
                      "gtm_token_dead": "gtm_reconnect", "gtm_container_mismatch": "gtm_container_mismatch",
                      "no_ga4_id": "no_ga4_id", "already_live": "already_live",
                      "gtm_no_permission": "gtm_no_permission", "gtm_workspace_busy": "gtm_workspace_busy"}


def _run_configure_conversion(client_id: int, params: dict, background_tasks):
    from agents.website_agent import configure_lead_conversion
    result = configure_lead_conversion(client_id, params.get("ga4_measurement_id", ""))
    if not result.get("success"):
        raise ActionFailed(_GTM_REFUSAL_CODES.get(result.get("reason"), "refused_by_action"),
                           "; ".join(map(str, result.get("errors") or ["unknown"])))
    return result


def _run_demo(fn_name: str):
    def run(client_id: int, params: dict, background_tasks):
        from core import verification_demo
        try:
            return getattr(verification_demo, fn_name)(client_id)
        except verification_demo.DemoRefused as e:
            raise ActionFailed("refused_by_action", str(e))
    return run


def _v_short_text(value, limit: int):
    text = " ".join(_TITLE_DROP.sub(" ", str(value or "")).split())[:limit].strip()
    if len(text) < 2:
        raise ValueError("text too short")
    return text


def _prepare_publish(client_id: int, params: dict, context: dict, rows: list) -> dict:
    """The metadata step before the card (agents/youtube_metadata): the title,
    description, hashtags, tags, category and language the client approves."""
    from agents import youtube_metadata
    return youtube_metadata.prepare_publish(
        client_id, params, {**(context or {}), "file_name": _media_name(client_id, params["drive_file_id"])},
        rows)


def _validate_publish_stored(client_id: int, params: dict) -> dict:
    """The STORED proposal, re-checked at proposal time and again at the
    click: the metadata exactly as validation allows (youtube_metadata) and
    the file still a video in the client's own media folder."""
    from agents import youtube_metadata
    youtube_metadata.revalidate_stored(client_id, params)
    _v_media_video(params.get("drive_file_id"), client_id)
    return params


def _publish_details(client_id: int, params: dict) -> dict:
    """What the client sees on the card — everything that will go to YouTube."""
    return {"file": _media_name(client_id, params.get("drive_file_id", "")),
            "title": params.get("title", ""), "description": params.get("description", ""),
            "hashtags": params.get("hashtags") or [], "tags": params.get("tags") or [],
            "links": [{"label": l.get("label", ""), "url": l.get("page", "")} for l in params.get("links") or []],
            "revision": params.get("revision", 0)}


def _run_publish_youtube_from_media(client_id: int, params: dict, background_tasks):
    """youtube_content_agent.publish — the function behind
    POST /api/youtube-content/publish and core/verification_demo — with the
    metadata exactly as the client approved it (params are the STORED,
    re-validated proposal). Privacy is not a param anywhere on this path:
    youtube_service.upload_video_stream always uploads private."""
    from agents.youtube_content_agent import DAILY_UPLOADS_PER_CLIENT, publish, uploads_today
    if uploads_today(client_id) >= DAILY_UPLOADS_PER_CLIENT:
        raise ActionFailed("daily_cap", f"{DAILY_UPLOADS_PER_CLIENT} uploads today")
    file_id = params["drive_file_id"]
    name = _media_name(client_id, file_id)
    title = params["title"]
    result = publish(client_id, {"drive_file_id": file_id, "title": title,
                                 "description": params["description"], "tags": params.get("tags") or [],
                                 "category_id": params.get("category_id", ""),
                                 "default_language": params.get("default_language", "")})
    if not result.get("success"):
        raise ActionFailed("refused_by_action", "; ".join(map(str, result.get("errors") or ["unknown"])))
    video_id = result.get("video_id", "")
    return {"video_id": video_id, "title": title, "privacy_status": "private", "source_file": name,
            "studio_url": f"https://studio.youtube.com/video/{video_id}/edit" if video_id else ""}


def _prepare_tiktok(client_id: int, params: dict, context: dict, rows: list) -> dict:
    """Before the card: the same video already has an open card → that card;
    then the grant, checked LIVE when nothing is recorded yet (one token
    refresh, tiktok_content_agent.check_grant). A missing, dead or
    video.upload-less grant never gets a send card the click would fail on —
    it switches to the connect_tiktok card with a plain line saying why."""
    for row in rows:
        if (row.get("params") or {}).get("drive_file_id") == params["drive_file_id"]:
            return {"existing": row}
    from agents.tiktok_content_agent import check_grant
    grant = check_grant(client_id)
    if grant in ("not_connected", "missing_upload", "dead"):
        notice = _TIKTOK_CONNECT_FIRST if grant == "not_connected" else _TIKTOK_RECONNECT_FIRST
        text = _pick(notice, (context or {}).get("language") or "he")
        return {"switch_to": "connect_tiktok",
                "notice": text.replace("{file}", _media_name(client_id, params["drive_file_id"]) or "—")}
    return {"params": {"drive_file_id": params["drive_file_id"]}}


def _validate_tiktok_stored(client_id: int, params: dict) -> dict:
    params = params or {}
    if set(params) != {"drive_file_id"}:
        raise ValueError("stored TikTok proposal has unexpected params")
    return {"drive_file_id": _v_tiktok_video(params.get("drive_file_id"), client_id)}


def _run_send_tiktok_to_inbox(client_id: int, params: dict, background_tasks):
    """tiktok_content_agent.publish — the function behind
    POST /api/tiktok-content/publish: Upload-to-Inbox (video.upload), then
    status polling until SEND_TO_USER_INBOX. There is no Direct Post on this
    path and no caption: the owner writes it and publishes inside TikTok."""
    from agents.tiktok_content_agent import publish
    file_id = params["drive_file_id"]
    name = _media_name(client_id, file_id)
    result = publish(client_id, {"drive_file_id": file_id})
    if not result.get("success"):
        for flag in ("needs_reconnect", "pending_limit", "too_large"):
            if result.get(flag):
                raise ActionFailed(flag, "; ".join(map(str, result.get("errors") or [flag])))
        raise ActionFailed("refused_by_action", "; ".join(map(str, result.get("errors") or ["unknown"])))
    return {"publish_id": result.get("publish_id", ""), "status": result.get("status", ""),
            "source_file": name}


def _prepare_outreach(client_id: int, params: dict, context: dict, rows: list) -> dict:
    """The chat's draft → the exact e-mail the client approves
    (core/gmail_outreach.prepare_draft: the business's own site is read, the
    address must be on it, the draft must be personal; the opt-out line is
    added). A draft that can't pass becomes a plain line, never a card."""
    from core import gmail_outreach
    lang = (context or {}).get("language") or "he"
    to_email = str((params or {}).get("to_email") or "").strip().lower()
    for row in rows or []:
        if (row.get("params") or {}).get("to_email") == to_email:
            return {"existing": row}          # one open draft per business
    try:
        prepared = gmail_outreach.prepare_draft(client_id, params, lang, rows)
    except gmail_outreach.DraftRefused as e:
        _log(f"client {client_id}: outreach draft refused ({e.code}): {e}", client_id=client_id)
        return {"refuse": _pick(_OUTREACH_DRAFT_REFUSALS.get(e.code, _OUTREACH_DRAFT_REFUSALS["not_personal"]),
                                lang)}
    # The Outreach page's "found" row (best effort; our own data only)
    gmail_outreach.record_found(client_id, prepared["to_email"], prepared["source_url"],
                                prepared["business_name"], source="chat")
    return {"params": prepared}


def _validate_outreach_stored(client_id: int, params: dict) -> dict:
    from core import gmail_outreach
    return gmail_outreach.revalidate_stored(params)


def _run_send_outreach_email(client_id: int, params: dict, background_tasks):
    """gmail_outreach.send_approved: every outreach rule again, the cost-budget
    guard, then users.messages.send from the client's own Gmail, recorded."""
    from core import gmail_outreach
    try:
        return gmail_outreach.send_approved(client_id, params)
    except gmail_outreach.SendFailed as e:
        raise ActionFailed(e.code if e.code in _OUTREACH_SEND_REFUSAL_CODES else "refused_by_action", str(e))


def _prepare_followup(client_id: int, params: dict, context: dict, rows: list) -> dict:
    """The ONE follow-up (gmail_outreach.prepare_followup): name, address and
    source from our record of the first message; refused with a plain line
    when outreach_policy.followup_refusal says no."""
    from core import gmail_outreach
    lang = (context or {}).get("language") or "he"
    try:
        return {"params": gmail_outreach.prepare_followup(client_id, params, lang, rows)}
    except gmail_outreach.DraftRefused as e:
        _log(f"client {client_id}: follow-up draft refused ({e.code}): {e}", client_id=client_id)
        return {"refuse": _pick(_FOLLOWUP_DRAFT_REFUSALS.get(e.code, _OUTREACH_DRAFT_REFUSALS["not_personal"]),
                                lang)}


def _run_send_outreach_followup(client_id: int, params: dict, background_tasks):
    from core import gmail_outreach
    try:
        return gmail_outreach.send_followup_approved(client_id, params)
    except gmail_outreach.SendFailed as e:
        raise ActionFailed(e.code if e.code in _OUTREACH_SEND_REFUSAL_CODES else "refused_by_action", str(e))


def _outreach_drafts_possible(state: dict, client: dict) -> bool:
    """Drafts need only the Gmail agent package (server-side, fails closed) —
    NOT the send flag or a Gmail connection: the client sees and approves
    nothing until sending is on (`_outreach_send_blocked` keeps the approve
    button off), but the draft is real and shows on the Outreach page."""
    from core import gmail_agent_plan
    return gmail_agent_plan.entitled(int((client or {}).get("id") or 0))


def _followup_possible(state: dict, client: dict) -> bool:
    return bool(((state or {}).get("outreach") or {}).get("followup_due")) and _outreach_drafts_possible(state, client)


def _outreach_send_blocked(client_id: int) -> str:
    """Why Approve can't run right now ('' = it can): sending switched off,
    or no Gmail connection. Checked at the card AND at the click, before the
    card is claimed — a blocked click leaves the draft as it was."""
    from core.round2_flags import gmail_send_enabled
    if not gmail_send_enabled():
        return "send_disabled"
    from core import gmail_outreach
    return "" if gmail_outreach.connected(client_id) else "needs_reconnect"


def _gmail_connect_needed(state: dict, client: dict) -> bool:
    """Flag on, not connected, and the client has the Gmail agent package
    (gmail_agent_plan.entitled — server-side, fails closed)."""
    from core.round2_flags import gmail_send_enabled
    if not gmail_send_enabled() or _connected("gmail_send")(state, client):
        return False
    from core import gmail_agent_plan
    return gmail_agent_plan.entitled(int((client or {}).get("id") or 0))


def _search_console_connect_needed(state: dict, client: dict) -> bool:
    from core.round2_flags import search_console_enabled
    if not search_console_enabled() or _connected("search_console")(state, client):
        return False
    return (_cards(state).get("organic_seo") or {}).get("status") not in (None, "not_in_plan")


def _run_rotate_wordpress_password(client_id: int, params: dict, background_tasks):
    """website_agent.rotate_stored_password — the same mint/delete code as
    one-click connect. The audit record is this action's own row (who
    approved, when, outcome); neither password ever reaches it or the log."""
    from agents.website_agent import rotate_stored_password
    result = rotate_stored_password(client_id)
    if not result.get("success"):
        raise ActionFailed("refused_by_action", "; ".join(map(str, result.get("errors") or ["unknown"])))
    try:
        from core import php_exposure
        php_exposure.note_rotated(client_id, result["site_url"], result.get("old_revoked"))
    except Exception as e:
        _log(f"client {client_id}: rotation not noted on the PHP2Shell record: {type(e).__name__}",
             severity="WARNING", client_id=client_id)
    _log(f"client {client_id}: WordPress Application Password rotated on {result['site_url']} "
         f"(old revoked: {bool(result.get('old_revoked'))})", client_id=client_id, action="rotate_wordpress_password")
    return {"site_url": result["site_url"],
            "old_password_revoked": "yes" if result.get("old_revoked")
            else "NO — delete the older 'uallak' Application Password in wp-admin → Users → Profile"}


def _run_approve_suggestion(client_id: int, params: dict, background_tasks):
    from agents.engagement_agent import decide_suggestion
    result = decide_suggestion(client_id, params["suggestion_id"], "approved", background_tasks)
    if not result.get("success"):
        raise ActionFailed("refused_by_action", str(result.get("error") or "unknown"))
    return result


# ─── Availability: does offering this make sense for THIS client now? ───────

def _cards(state: dict) -> dict:
    return ((state or {}).get("channels") or {}).get("by_channel") or {}


def _channel_needs_connect(channel: str):
    def check(state: dict, client: dict) -> bool:
        card = _cards(state).get(channel) or {}
        return (card.get("status") == "not_connected"
                or (card.get("status") == "needs_attention" and card.get("reason") == "reconnect"))
    return check


def _creative_tool_missing(state: dict, client: dict) -> bool:
    """Some creative tool (core/vendor_tools) is not usable right now."""
    tools = ((state or {}).get("creative_tools") or {}).get("tools") or {}
    return any(t.get("status") in ("not_connected", "key_rejected") for t in tools.values())


def _creative_tools_spec() -> dict:
    from core.vendor_tools import TOOLS
    return TOOLS


def _v_creative_tool(value, client_id: int):
    tool = str(value or "").strip().lower()
    if tool not in _creative_tools_spec():
        raise ValueError("tool must be heygen, elevenlabs or higgsfield")
    return tool


def _creative_label(tool: str, lang: str = "he") -> str:
    """The tool as the CLIENT reads it — never the vendor's name."""
    from core.vendor_tools import client_label
    return client_label(tool, lang) or "—"


def _creative_focus(tool: str) -> str:
    from core.vendor_tools import focus_id
    return focus_id(tool) if tool in _creative_tools_spec() else "connGrid"


def _creative_tools_state(state: dict) -> dict:
    return ((state or {}).get("creative_tools") or {}).get("tools") or {}


def _tool_usable(state: dict, tool: str) -> bool:
    return (_creative_tools_state(state).get(tool) or {}).get("status") in ("connected", "connected_unverified")


def _voice_clone_possible(state: dict, client: dict) -> bool:
    """The voice tool connected, the cloning consent given, a recording to
    clone from, and no clone yet."""
    voice = (state or {}).get("voice") or {}
    return (_tool_usable(state, "elevenlabs") and bool(voice.get("voice_clone_consent"))
            and (voice.get("count") or 0) > 0 and not voice.get("voice_clone_exists"))


def _presenter_video_possible(state: dict, client: dict) -> bool:
    """A presenter tool connected and a voice to speak with (a recording or
    the clone). Consent, tier and minutes are re-checked when it runs."""
    voice = (state or {}).get("voice") or {}
    return ((_tool_usable(state, "heygen") or _tool_usable(state, "higgsfield"))
            and ((voice.get("count") or 0) > 0 or bool(voice.get("voice_clone_exists"))))


def _v_script(value, client_id: int):
    text = " ".join(str(value or "").split())
    if not 3 <= len(text) <= 1500:
        raise ValueError("script must be 3-1500 characters")
    return text


def _v_presenter_voice(value, client_id: int):
    """"clone" or "recording:<file_id>" — the recording must be in THIS
    client's voice folder (core/voice_recordings)."""
    raw = str(value or "").strip()
    if raw == "clone":
        return raw
    if raw.startswith("recording:"):
        from core import voice_recordings
        if voice_recordings.get(client_id, raw.split(":", 1)[1].strip()):
            return "recording:" + raw.split(":", 1)[1].strip()
    raise ValueError("voice must be clone or recording:<file_id from account_state.voice.recordings>")


def _v_presenter(value, client_id: int):
    from core.avatar_video import PROVIDERS
    provider = str(value or "").strip().lower()
    if provider not in PROVIDERS:
        raise ValueError("provider must be heygen or higgsfield")
    return provider


_VOICE_PHRASE = {
    "clone": {"he": "הקול המשובט שלך", "en": "your cloned voice", "fr": "votre voix clonée",
              "ar": "صوتك المستنسخ", "ru": "ваш клонированный голос"},
    "recording": {"he": "ההקלטה \"{name}\"", "en": "the recording \"{name}\"", "fr": "l'enregistrement « {name} »",
                  "ar": "التسجيل \"{name}\"", "ru": "запись «{name}»"},
}


def _presenter_display(client_id: int, params: dict) -> dict:
    """The card's words for WHO speaks and WITH WHAT voice — client labels,
    never a vendor name."""
    from core import vendor_tools
    from core.avatar_video import PROVIDERS
    lang = _lang(client_id)
    provider = params.get("provider") or vendor_tools.default_provider(client_id)
    tool = getattr(PROVIDERS.get(provider), "tool", "heygen")
    voice = params.get("voice", "")
    if voice.startswith("recording:"):
        from core import voice_recordings
        name = (voice_recordings.get(client_id, voice.split(":", 1)[1]) or {}).get("name") or "—"
        phrase = _pick(_VOICE_PHRASE["recording"], lang).replace("{name}", name)
    else:
        phrase = _pick(_VOICE_PHRASE["clone"], lang)
    return {"presenter": vendor_tools.client_label(tool, lang), "voice": phrase}


def _run_clone_voice(client_id: int, params: dict, background_tasks):
    """avatar_agent.create_voice_clone — the function behind
    POST /api/avatar/create-voice; it re-checks the cloning consent, the
    tool and the recordings itself. Its outcome line is posted by decide()."""
    from agents.avatar_agent import create_voice_clone
    result = create_voice_clone(client_id, None, notify=False)
    if result.get("success"):
        return {"voice_created": True}
    code = result.get("code", "")
    raise ActionFailed({"missing_tool": "missing_tool", "no_voice_consent": "not_ready_yet",
                        "no_recordings": "not_ready_yet"}.get(code, "provider_error"),
                       f"{code}: {result.get('message_he') or '; '.join(map(str, result.get('errors') or []))}")


def _run_make_presenter_video(client_id: int, params: dict, background_tasks):
    """avatar_agent.generate_avatar_video — the function behind
    POST /api/avatar/generate-video: consent → tier → minutes → the provider
    layer (core/avatar_video). The video lands in the client's Drive for
    review; nothing is published."""
    from agents.avatar_agent import generate_avatar_video
    voice_param = params.get("voice", "")
    voice = ({"kind": "recording", "file_id": voice_param.split(":", 1)[1]} if voice_param.startswith("recording:")
             else {"kind": "clone"})
    result = generate_avatar_video(client_id, params["script"], "", "", params.get("provider", ""), voice)
    if result.get("success"):
        return {"link": result.get("link", ""), "duration_seconds": result.get("duration_seconds")}
    code = result.get("code", "")
    if code == "missing_tool":
        mapped = "missing_tool"
    elif code in ("no_tier", "minutes_cap"):
        mapped = "not_in_plan"
    elif code in ("", "provider_error", "elevenlabs_error", "error"):
        mapped = "provider_error"   # ours to fix: _FAILED + an admin alert
    else:
        mapped = "not_ready_yet"
    raise ActionFailed(mapped, f"{code}: {result.get('message_he') or '; '.join(map(str, result.get('errors') or []))}")


def _gtm_needs_connect(state: dict, client: dict) -> bool:
    tracking = (state or {}).get("tracking") or {}
    if tracking.get("connected"):
        return bool(tracking.get("needs_reconnect"))
    return _channel_needs_connect("website_tracking")(state, client)


def _conversion_setup_possible(state: dict, client: dict) -> bool:
    tracking = (state or {}).get("tracking") or {}
    return (bool(tracking.get("connected")) and not tracking.get("needs_reconnect")
            and tracking.get("lead_event_live") is not True)


def _connected(platform: str):
    def check(state: dict, client: dict) -> bool:
        return platform in (((state or {}).get("connections") or {}).get("active_platforms") or [])
    return check


def _has_pending_suggestions(state: dict, client: dict) -> bool:
    return bool(((state or {}).get("approvals") or {}).get("waiting_for_client"))


def _youtube_upload_possible(state: dict, client: dict) -> bool:
    """YouTube connected with a working grant, at least one video in the media
    folder within the size cap, and today's cap not reached (when known)."""
    youtube = _cards(state).get("youtube") or {}
    if youtube.get("status") not in ("active", "connected_no_data"):
        return False
    if not _connected("youtube")(state, client):
        return False
    media = (state or {}).get("media_folder") or {}
    if not any(v.get("uploadable") for v in media.get("videos") or []):
        return False
    cap = media.get("youtube_daily_upload_cap")
    return cap is None or (media.get("youtube_uploads_today") or 0) < cap


def _tiktok_send_possible(state: dict, client: dict) -> bool:
    """TikTok is in the client's plan or connected (any status but
    not_in_plan), and a video in the media folder fits the inbox upload.
    Deliberately NOT gated on a healthy connection: asking to send with a
    missing or broken one must reach _prepare_tiktok, which answers with the
    reconnect card and a plain line instead of silence."""
    tiktok = _cards(state).get("tiktok") or {}
    if not tiktok or tiktok.get("status") == "not_in_plan":
        return False
    media = (state or {}).get("media_folder") or {}
    return any(v.get("tiktok_uploadable") for v in media.get("videos") or [])


# ─── The whitelist ───────────────────────────────────────────────────────────
# approver: "client" (one click by the client) | "admin" (Control Room).
# client_requestable: an admin action the CLIENT chat may still propose — it
#   becomes a staff request, never a client button.
# kind: "execute" (server runs it) | "link" (the click navigates to an OAuth
#   start route — the consent itself is the client's) | "focus" (the click
#   opens a dashboard card that carries a consent the click must not bypass).

def _txt(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


ACTIONS = {
    "configure_conversion": {
        # The client's own click approves it: it is THEIR container, the card
        # says exactly what is added, and the guards (container match, scopes,
        # dead grant, already live, own workspace) refuse before any write.
        # The Control Room chat may propose it too (admin_proposable), then
        # staff approve it there.
        "approver": "client", "admin_proposable": True, "client_requestable": True,
        "internal_only": False, "kind": "execute",
        "does": "Adds ONE form-submission trigger and ONE GA4 generate_lead event tag to the client's "
                "Tag Manager container, creates a version and PUBLISHES it, so leads from their site "
                "are counted. Refused if the site loads a different container, the connection needs "
                "renewing, or a lead event is already live.",
        "params": {"ga4_measurement_id": {"required": False, "validate": _v_ga4,
                                          "hint": "G-XXXXXXX, only if known from the data; empty = detected from the site"}},
        "available": _conversion_setup_possible,
        "run": _run_configure_conversion,
        "title": _txt("הפעלת מדידת לידים באתר", "Turn on lead tracking on your website",
                      "Activer le suivi des prospects sur votre site", "تفعيل قياس العملاء المحتملين في موقعكم",
                      "Включить учёт заявок на сайте"),
        "what": _txt("נוסיף ל-Tag Manager של האתר מדידה של שליחת טופס (אירוע generate_lead) ונפרסם אותה, כך שכל ליד מהאתר ייספר. תגים קיימים לא משתנים.",
                     "We'll add form-submission tracking (a generate_lead event) to your site's Tag Manager and publish it, so every lead from your site is counted. Existing tags stay as they are.",
                     "Nous ajouterons le suivi des envois de formulaire (événement generate_lead) au Tag Manager de votre site et le publierons, pour compter chaque prospect. Les balises existantes ne changent pas.",
                     "سنضيف قياس إرسال النماذج (حدث generate_lead) إلى Tag Manager في موقعكم وننشره، لكي يُحتسب كل عميل محتمل. الوسوم الحالية لا تتغير.",
                     "Мы добавим в Tag Manager вашего сайта учёт отправки форм (событие generate_lead) и опубликуем его, чтобы считалась каждая заявка. Существующие теги не меняются."),
        "done": _txt("מדידת הלידים באתר פעילה ✓ מעכשיו כל שליחת טופס נספרת.",
                     "Lead tracking on your site is live ✓ Every form submission is counted from now on.",
                     "Le suivi des prospects est actif ✓ Chaque envoi de formulaire est désormais compté.",
                     "قياس العملاء المحتملين فعّال ✓ من الآن يُحتسب كل إرسال نموذج.",
                     "Учёт заявок на сайте включён ✓ Теперь считается каждая отправка формы."),
        "done_message": lambda result, lang: (
            _pick(ACTIONS["configure_conversion"]["done"], lang)
            + (f"\nTag Manager: {result['tag_name']} · {result['live_version']}"
               if result.get("tag_name") and result.get("live_version") else "")),
        "refusals": {
            "gtm_reconnect": _txt(
                "החיבור ל-Tag Manager צריך חידוש לפני שאפשר להוסיף את המדידה — שום דבר לא השתנה באתר. לחצו על הכפתור כדי לחבר מחדש (פעם אחת, מול Google), ואז נסו שוב.",
                "Your Tag Manager connection needs renewing before tracking can be added — nothing on your site changed. Tap the button to reconnect (once, with Google), then try again.",
                "Votre connexion Tag Manager doit être renouvelée — rien n'a changé sur votre site. Touchez le bouton pour reconnecter (une fois, avec Google), puis réessayez.",
                "يجب تجديد الربط مع Tag Manager قبل إضافة القياس — لم يتغير شيء في موقعكم. اضغطوا على الزر لإعادة الربط (مرة واحدة مع Google) ثم حاولوا مجددًا.",
                "Подключение Tag Manager нужно обновить — на сайте ничего не изменилось. Нажмите кнопку, чтобы переподключить (один раз, через Google), и повторите."),
            "gtm_container_mismatch": _txt(
                "ה-Tag Manager שחובר הוא לא זה שמותקן באתר שלכם, אז לא הוספנו כלום. בכרטיס \"מדידה באתר\" בחרו את הקונטיינר הנכון (החלפת קונטיינר), ואז נסו שוב.",
                "The connected Tag Manager container isn't the one installed on your site, so nothing was added. In the website measurement card, choose the right container, then try again.",
                "Le conteneur Tag Manager connecté n'est pas celui de votre site — rien n'a été ajouté. Choisissez le bon conteneur dans la carte de mesure, puis réessayez.",
                "حاوية Tag Manager المرتبطة ليست المثبتة في موقعكم، لذلك لم نُضف شيئًا. اختاروا الحاوية الصحيحة في بطاقة القياس ثم حاولوا مجددًا.",
                "Подключён не тот контейнер Tag Manager, что стоит на сайте, — ничего не добавлено. Выберите нужный контейнер в карточке аналитики и повторите."),
            "no_ga4_id": _txt(
                "חסר לנו מזהה Google Analytics של האתר (מתחיל ב-G-), אז לא הוספנו כלום. אם יש לכם אותו — כתבו לי אותו כאן, ואכין את זה שוב.",
                "We need your site's Google Analytics ID (it starts with G-), so nothing was added. If you have it, write it here and I'll set this up again.",
                "Il nous faut l'identifiant Google Analytics du site (commence par G-) — rien n'a été ajouté. Écrivez-le ici et je relance.",
                "نحتاج معرّف Google Analytics للموقع (يبدأ بـ G-)، لذلك لم نُضف شيئًا. اكتبوه هنا وسأجهز ذلك مجددًا.",
                "Нужен идентификатор Google Analytics сайта (начинается с G-) — ничего не добавлено. Напишите его здесь, и я подготовлю заново."),
            "already_live": _txt(
                "מדידת לידים כבר פעילה ב-Tag Manager של האתר — לא הוספנו עוד אחת, כדי שכל ליד ייספר פעם אחת בלבד.",
                "Lead tracking is already live in your site's Tag Manager — we didn't add a second one, so every lead is counted once.",
                "Le suivi des prospects est déjà actif — nous n'en ajoutons pas un second, pour compter chaque prospect une seule fois.",
                "قياس العملاء المحتملين فعّال بالفعل — لم نُضف قياسًا ثانيًا كي يُحتسب كل عميل مرة واحدة.",
                "Учёт заявок уже работает — второй не добавлен, чтобы каждая заявка считалась один раз."),
            "gtm_no_permission": _txt(
                "לחשבון Google שחיברתם אין הרשאת פרסום בקונטיינר הזה ב-Tag Manager, אז לא השתנה כלום. בעל הקונטיינר צריך לתת לו הרשאת \"Publish\" (ב-Tag Manager: Admin ← User Management), ואז נסו שוב.",
                "The Google account you connected can't publish in this Tag Manager container, so nothing changed. The container owner needs to give it Publish permission (Tag Manager → Admin → User Management), then try again.",
                "Le compte Google connecté ne peut pas publier dans ce conteneur — rien n'a changé. Le propriétaire doit lui donner le droit Publier (Admin → Gestion des utilisateurs), puis réessayez.",
                "حساب Google المرتبط لا يملك صلاحية النشر في هذه الحاوية، لذلك لم يتغير شيء. يجب أن يمنحه مالك الحاوية صلاحية Publish (Admin ← User Management) ثم حاولوا مجددًا.",
                "У подключённого аккаунта Google нет права публикации в этом контейнере — ничего не изменилось. Владелец должен выдать право Publish (Admin → User Management), затем повторите."),
            "gtm_workspace_busy": _txt(
                "ב-Tag Manager של האתר יש שינויים שעוד לא פורסמו, ואין מקום לסביבת עבודה נוספת — כדי לא לפרסם שינויים של מישהו אחר, לא נגענו בכלום. פרסמו או בטלו את השינויים ב-Tag Manager (או מחקו סביבת עבודה שלא בשימוש), ואז נסו שוב.",
                "Your site's Tag Manager has unpublished changes and no room for another workspace — to avoid publishing someone else's changes, we touched nothing. Publish or discard those changes (or delete an unused workspace), then try again.",
                "Le Tag Manager du site a des modifications non publiées et aucun espace de travail libre — nous n'avons rien touché. Publiez-les ou annulez-les (ou supprimez un espace inutilisé), puis réessayez.",
                "في Tag Manager تغييرات غير منشورة ولا مكان لمساحة عمل إضافية — كي لا ننشر تغييرات غيرنا لم نلمس شيئًا. انشروا التغييرات أو ألغوها (أو احذفوا مساحة غير مستخدمة) ثم حاولوا مجددًا.",
                "В Tag Manager есть неопубликованные изменения и нет места для ещё одного рабочего пространства — чтобы не опубликовать чужое, мы ничего не трогали. Опубликуйте или отмените их (или удалите лишнее пространство) и повторите."),
        },
        "follow_up": {"gtm_reconnect": "connect_tag_manager"},
    },
    "demo_google_ads_campaign": {
        "approver": "admin", "client_requestable": False, "internal_only": True, "kind": "execute",
        "does": "Google verification demo: creates ONE test search campaign in the connected Google Ads "
                "account, created PAUSED with a 5 ILS/day budget (spends nothing unless enabled). "
                "Internal test clients only.",
        "params": {},
        "available": _connected("google_ads"),
        "run": _run_demo("create_test_campaign"),
        "title": _txt("קמפיין בדיקה ב-Google Ads (מושהה)", "Test Google Ads campaign (paused)",
                      "Campagne test Google Ads (en pause)", "حملة اختبار في Google Ads (متوقفة)",
                      "Тестовая кампания Google Ads (на паузе)"),
        "what": _txt("ייווצר בחשבון Google Ads של לקוח הבדיקה קמפיין חיפוש אחד במצב PAUSED, תקציב 5 ₪ ליום — לא רץ ולא מוציא כסף.",
                     "Creates one search campaign in the test client's Google Ads account, status PAUSED, 5 ILS/day — it does not run or spend.",
                     "Crée une campagne de recherche en PAUSE (5 ILS/jour) — elle ne tourne pas.",
                     "ينشئ حملة بحث واحدة بحالة PAUSED بميزانية 5 شيكل يوميًا — لا تعمل ولا تنفق.",
                     "Создаёт одну поисковую кампанию в статусе PAUSED, 5 ILS/день — она не работает и не тратит."),
        "done": _txt("קמפיין הבדיקה נוצר — מושהה ✓", "Test campaign created — paused ✓",
                     "Campagne test créée — en pause ✓", "تم إنشاء حملة الاختبار — متوقفة ✓",
                     "Тестовая кампания создана — на паузе ✓"),
    },
    "google_ads_test_campaign": {
        # The CLIENT-chat twin of demo_google_ads_campaign, for the Google
        # verification video: the account owner asks in their own chat and
        # approves with their own click. Internal test clients only (the
        # campaign is a fixed test one); real clients' campaigns are drafted
        # and created by staff (google_ads_agent.create_search_campaign), and
        # every campaign, this one included, is created PAUSED.
        "approver": "client", "client_requestable": True, "internal_only": True, "kind": "execute",
        "does": "Creates ONE test search campaign in the client's connected Google Ads account, "
                "created PAUSED with a 5 ILS/day budget, so it spends nothing unless they enable it "
                "in Google Ads. The client approves it on the card.",
        "params": {},
        "available": _connected("google_ads"),
        "run": _run_demo("create_test_campaign"),
        "title": _txt("יצירת קמפיין בדיקה מושהה ב-Google Ads", "Create a paused test campaign in Google Ads",
                      "Créer une campagne test en pause dans Google Ads",
                      "إنشاء حملة اختبار متوقفة في Google Ads", "Создать тестовую кампанию на паузе в Google Ads"),
        "what": _txt("ייווצר בחשבון Google Ads שלכם קמפיין חיפוש אחד בשם \"uallak verification test\", במצב מושהה (PAUSED) ובתקציב 5 ₪ ליום. הוא לא רץ ולא מוציא כסף, אלא אם תפעילו אותו בעצמכם ב-Google Ads.",
                     "One search campaign named \"uallak verification test\" will be created in your Google Ads account, PAUSED, with a 5 ILS/day budget. It doesn't run or spend unless you enable it yourself in Google Ads.",
                     "Une campagne de recherche « uallak verification test » sera créée dans votre compte Google Ads, en PAUSE, budget 5 ILS/jour. Elle ne tourne pas sauf si vous l'activez.",
                     "ستُنشأ في حساب Google Ads حملة بحث واحدة باسم \"uallak verification test\" بحالة متوقفة وميزانية 5 شيكل يوميًا. لا تعمل ولا تنفق إلا إذا فعّلتموها بأنفسكم.",
                     "В вашем Google Ads будет создана поисковая кампания «uallak verification test» в статусе PAUSED, 5 ILS/день. Она не работает и не тратит, пока вы сами её не включите."),
        "done": _txt("קמפיין הבדיקה נוצר בחשבון Google Ads שלכם — מושהה ✓ הוא לא רץ ולא מוציא כסף.",
                     "The test campaign was created in your Google Ads account — paused ✓ It doesn't run or spend.",
                     "La campagne test a été créée — en pause ✓ Elle ne dépense rien.",
                     "تم إنشاء حملة الاختبار في حساب Google Ads — متوقفة ✓ لا تعمل ولا تنفق.",
                     "Тестовая кампания создана в Google Ads — на паузе ✓ Она не тратит деньги."),
        "done_message": lambda result, lang: (
            _pick(ACTIONS["google_ads_test_campaign"]["done"], lang)
            + "".join(f"\n{label}: {result[key]}" for key, label in
                      (("campaign_name", "Campaign"), ("campaign_id", "ID"), ("status", "Status"))
                      if result.get(key))),
    },
    "demo_youtube_upload": {
        "approver": "admin", "client_requestable": False, "internal_only": True, "kind": "execute",
        "does": "Google verification demo: uploads the smallest video (within the upload size limit) from the client's Drive "
                "media folder to their YouTube channel as PRIVATE. Internal test clients only.",
        "params": {},
        "available": _connected("youtube"),
        "run": _run_demo("upload_test_video"),
        "title": _txt("סרטון בדיקה ל-YouTube (פרטי)", "Test YouTube upload (private)",
                      "Vidéo test YouTube (privée)", "رفع فيديو اختبار إلى YouTube (خاص)",
                      "Тестовое видео на YouTube (приватное)"),
        "what": _txt("הסרטון הקטן ביותר מתיקיית המדיה של לקוח הבדיקה יועלה לערוץ שלו כפרטי (Private) — אף אחד לא יראה אותו.",
                     "The smallest video in the test client's media folder is uploaded to their channel as Private — nobody else can see it.",
                     "La plus petite vidéo du dossier média est mise en ligne en Privé.",
                     "يُرفع أصغر فيديو من مجلد الوسائط إلى القناة كخاص.",
                     "Самое маленькое видео из папки медиа загружается на канал как приватное."),
        "done": _txt("הסרטון הועלה — פרטי ✓", "Video uploaded — private ✓", "Vidéo mise en ligne — privée ✓",
                     "تم رفع الفيديو — خاص ✓", "Видео загружено — приватное ✓"),
    },
    "rotate_wordpress_password": {
        # PHP2Shell response (core/php_exposure, docs/INCIDENT-PHP2SHELL.md):
        # its memory-disclosure half can leak request headers, and ours carry
        # this password. Admin-only; the client is never asked or shown it.
        "approver": "admin", "client_requestable": False, "internal_only": False, "kind": "execute",
        "does": "Replaces the WordPress Application Password we store for the client's site: mints a new "
                "one on the site, checks it works, stores it encrypted, then deletes the old one there. "
                "For sites that may have run a PHP version vulnerable to PHP2Shell. Best after the host "
                "has updated PHP — on a still-vulnerable server the new one can leak the same way.",
        "params": {},
        "available": _connected("wordpress"),
        "run": _run_rotate_wordpress_password,
        "title": _txt("החלפת סיסמת האפליקציה של וורדפרס", "Rotate the stored WordPress password",
                      "Renouveler le mot de passe WordPress enregistré", "تجديد كلمة مرور WordPress المخزّنة",
                      "Заменить сохранённый пароль WordPress"),
        "what": _txt("ניצור באתר סיסמת אפליקציה חדשה, נוודא שהיא עובדת, נשמור אותה מוצפנת ונמחק את הישנה מהאתר. החיבור לאתר לא נקטע.",
                     "We mint a new application password on the site, check it works, store it encrypted and delete the old one there. The site connection is not interrupted.",
                     "Nous créons un nouveau mot de passe d'application sur le site, le vérifions, l'enregistrons chiffré et supprimons l'ancien. La connexion n'est pas interrompue.",
                     "ننشئ كلمة مرور تطبيق جديدة في الموقع، نتحقق منها، نخزنها مشفّرة ونحذف القديمة. الاتصال بالموقع لا ينقطع.",
                     "Создаём на сайте новый пароль приложения, проверяем его, сохраняем в зашифрованном виде и удаляем старый. Подключение к сайту не прерывается."),
        "done": _txt("סיסמת האפליקציה של וורדפרס הוחלפה ✓", "WordPress application password rotated ✓",
                     "Mot de passe d'application WordPress renouvelé ✓", "تم تجديد كلمة مرور تطبيق WordPress ✓",
                     "Пароль приложения WordPress заменён ✓"),
    },
    "connect_tag_manager": {
        "approver": "client", "client_requestable": True, "internal_only": False, "kind": "link",
        "link": "/api/oauth/gtm/start",
        "does": "Opens Google's approval screen to (re)connect the client's Tag Manager — the fix when "
                "tracking is not connected, expired, lost access or lacks permissions.",
        "params": {},
        "available": _gtm_needs_connect,
        "title": _txt("חיבור מחדש של מדידת האתר", "Reconnect your website tracking",
                      "Reconnecter le suivi du site", "إعادة ربط قياس الموقع", "Переподключить аналитику сайта"),
        "what": _txt("ייפתח מסך האישור של Google — מאשרים פעם אחת, וחוזרים לכאן.",
                     "Google's approval screen opens — approve once and you're back here.",
                     "L'écran d'autorisation de Google s'ouvre — validez une fois et revenez ici.",
                     "ستفتح شاشة موافقة Google — وافقوا مرة واحدة وتعودون إلى هنا.",
                     "Откроется окно подтверждения Google — подтвердите один раз и вернитесь сюда."),
        "done": _txt("פותח את מסך האישור של Google…", "Opening Google's approval screen…",
                     "Ouverture de l'écran Google…", "جارٍ فتح شاشة موافقة Google…", "Открываю окно Google…"),
    },
    "connect_youtube": {
        "approver": "client", "client_requestable": True, "internal_only": False, "kind": "link",
        "link": "/api/oauth/youtube/start",
        "does": "Opens Google's approval screen to (re)connect the client's YouTube channel.",
        "params": {},
        "available": _channel_needs_connect("youtube"),
        "title": _txt("חיבור ערוץ YouTube", "Connect your YouTube channel", "Connecter votre chaîne YouTube",
                      "ربط قناة YouTube", "Подключить канал YouTube"),
        "what": _txt("ייפתח מסך האישור של Google לערוץ — מאשרים פעם אחת, וחוזרים לכאן.",
                     "Google's approval screen for your channel opens — approve once and you're back here.",
                     "L'écran d'autorisation Google s'ouvre — validez une fois.",
                     "ستفتح شاشة موافقة Google للقناة — وافقوا مرة واحدة.",
                     "Откроется окно подтверждения Google для канала — подтвердите один раз."),
        "done": _txt("פותח את מסך האישור של Google…", "Opening Google's approval screen…",
                     "Ouverture de l'écran Google…", "جارٍ فتح شاشة موافقة Google…", "Открываю окно Google…"),
    },
    "connect_meta": {
        "approver": "client", "client_requestable": True, "internal_only": False, "kind": "link",
        "link": "/api/oauth/meta/start",
        "does": "Opens Meta's approval screen to (re)connect the client's Facebook/Instagram.",
        "params": {},
        "available": _channel_needs_connect("meta"),
        "title": _txt("חיבור פייסבוק ואינסטגרם", "Connect Facebook and Instagram", "Connecter Facebook et Instagram",
                      "ربط فيسبوك وإنستغرام", "Подключить Facebook и Instagram"),
        "what": _txt("ייפתח מסך האישור של Meta — מאשרים פעם אחת, וחוזרים לכאן.",
                     "Meta's approval screen opens — approve once and you're back here.",
                     "L'écran d'autorisation Meta s'ouvre — validez une fois.",
                     "ستفتح شاشة موافقة Meta — وافقوا مرة واحدة.",
                     "Откроется окно подтверждения Meta — подтвердите один раз."),
        "done": _txt("פותח את מסך האישור של Meta…", "Opening Meta's approval screen…",
                     "Ouverture de l'écran Meta…", "جارٍ فتح شاشة موافقة Meta…", "Открываю окно Meta…"),
    },
    "connect_google_ads": {
        # "focus", not a direct link: connecting Google Ads requires the
        # staff-access consent tick on its card (GOOGLE_ADS_STAFF_CONSENT, the
        # basis of the privacy policy's human-access section). The click opens
        # that card; it must never skip the consent.
        "approver": "client", "client_requestable": True, "internal_only": False, "kind": "focus",
        "focus": "gadsBtn",
        "does": "Opens the Google Ads connection card (it carries the staff-access consent the client "
                "must tick) so the client can (re)connect.",
        "params": {},
        "available": _channel_needs_connect("google_ads"),
        "title": _txt("חיבור Google Ads", "Connect Google Ads", "Connecter Google Ads", "ربط Google Ads",
                      "Подключить Google Ads"),
        "what": _txt("ייפתח כרטיס החיבור של Google Ads — מסמנים את תיבת ההסכמה ומאשרים מול Google.",
                     "The Google Ads card opens — tick the consent box and approve with Google.",
                     "La carte Google Ads s'ouvre — cochez le consentement et validez avec Google.",
                     "ستفتح بطاقة Google Ads — حددوا خانة الموافقة ووافقوا لدى Google.",
                     "Откроется карточка Google Ads — отметьте согласие и подтвердите в Google."),
        "done": _txt("פותח את כרטיס החיבור…", "Opening the connection card…", "Ouverture de la carte…",
                     "جارٍ فتح البطاقة…", "Открываю карточку…"),
    },
    "connect_tiktok": {
        "approver": "client", "client_requestable": True, "internal_only": False, "kind": "link",
        "link": "/api/oauth/tiktok/start",
        "does": "Opens TikTok's approval screen to (re)connect the client's TikTok account — the fix "
                "when TikTok is not connected or the connection needs renewing (also offered "
                "automatically when a TikTok send can't go through).",
        "params": {},
        "available": _channel_needs_connect("tiktok"),
        "title": _txt("חיבור חשבון טיקטוק", "Connect your TikTok account", "Connecter votre compte TikTok",
                      "ربط حساب تيك توك", "Подключить аккаунт TikTok"),
        "what": _txt("ייפתח מסך האישור של טיקטוק — מאשרים פעם אחת, וחוזרים לכאן.",
                     "TikTok's approval screen opens — approve once and you're back here.",
                     "L'écran d'autorisation TikTok s'ouvre — validez une fois et revenez ici.",
                     "ستفتح شاشة موافقة تيك توك — وافقوا مرة واحدة وتعودون إلى هنا.",
                     "Откроется окно подтверждения TikTok — подтвердите один раз и вернитесь сюда."),
        "done": _txt("פותח את מסך האישור של טיקטוק…", "Opening TikTok's approval screen…",
                     "Ouverture de l'écran TikTok…", "جارٍ فتح شاشة موافقة تيك توك…", "Открываю окно TikTok…"),
    },
    "connect_creative_tool": {
        # "focus": the key is pasted ONLY into that tool's own card (never in
        # chat), and HeyGen's card carries the likeness consent the click must
        # not bypass. Opens exactly the one tool's card (core/vendor_tools).
        "approver": "client", "client_requestable": True, "internal_only": False, "kind": "focus",
        "focus": lambda params: _creative_focus(params.get("tool", "")),
        "does": "Opens the connection card of ONE creative tool (heygen | elevenlabs | higgsfield) so the "
                "client pastes that tool's key there — the fix when a feature needs a tool that "
                "account_state.creative_tools shows as not connected or key_rejected.",
        "params": {"tool": {"required": True, "validate": _v_creative_tool,
                            "hint": "heygen | elevenlabs | higgsfield — the ONE tool that is missing"}},
        "available": _creative_tool_missing,
        "display": lambda client_id, params: {"tool": _creative_label(params.get("tool", ""), _lang(client_id))},
        "title": _txt("חיבור כלי יצירה", "Connect a creative tool", "Connecter un outil de création",
                      "ربط أداة إنشاء", "Подключить творческий инструмент"),
        "what": _txt("ייפתח רק הכרטיס \"{tool}\", עם הדרכה צעד אחר צעד. נתקעתם? יש שם כפתור ונעשה את זה יחד. שאר הכלים לא משתנים.",
                     "Only the \"{tool}\" card opens, with a step-by-step guide. Stuck? There's a button there and we'll do it with you. Your other tools stay as they are.",
                     "Seule la carte « {tool} » s'ouvre, avec un guide pas à pas. Bloqué ? Un bouton y est prévu et nous le faisons avec vous. Vos autres outils ne changent pas.",
                     "ستفتح بطاقة \"{tool}\" فقط، مع إرشاد خطوة بخطوة. علقتم؟ يوجد زر هناك وسنفعل ذلك معكم. باقي الأدوات لا تتغير.",
                     "Откроется только карточка «{tool}» с пошаговой инструкцией. Застряли? Там есть кнопка — сделаем вместе. Остальные инструменты не меняются."),
        "done": _txt("פותח את כרטיס החיבור…", "Opening the connection card…", "Ouverture de la carte…",
                     "جارٍ فتح البطاقة…", "Открываю карточку…"),
    },
    # ── Creative jobs on the client's OWN tool accounts. internal_only until
    # the live test on client 27 passes (docs/CREATIVE-TOOLS.md): nothing a
    # client is offered may be a path that never ran live. ──
    "clone_voice": {
        "approver": "client", "client_requestable": True, "internal_only": True, "kind": "execute",
        "does": "Clones the client's voice from the recordings in account_state.voice, on THEIR own voice "
                "tool account. Only when voice_clone_consent is true and no clone exists yet.",
        "params": {},
        "available": _voice_clone_possible,
        "run": _run_clone_voice,
        "slow": {"minutes": 10, "eta": _txt(
            "זה לוקח בדרך כלל דקה-שתיים.", "It usually takes a minute or two.",
            "Cela prend généralement une à deux minutes.", "يستغرق ذلك عادة دقيقة أو دقيقتين.",
            "Обычно это занимает минуту-две.")},
        "title": _txt("שיבוט הקול שלך", "Clone your voice", "Cloner votre voix", "استنساخ صوتك",
                      "Клонировать ваш голос"),
        "what": _txt("מההקלטות שהעליתם ייווצר שיבוט של הקול שלכם, בחשבון שלכם. אחר כך הסרטונים יוכלו לדבר בקול שלכם. אפשר למחוק אותו בכל רגע.",
                     "A clone of your voice will be made from your recordings, on your own account. Videos can then speak in your voice. You can delete it at any time.",
                     "Un clone de votre voix sera créé à partir de vos enregistrements, sur votre propre compte. Les vidéos pourront ensuite parler avec votre voix. Supprimable à tout moment.",
                     "سيُنشأ من تسجيلاتكم استنساخ لصوتكم في حسابكم. بعدها يمكن للفيديوهات أن تتكلم بصوتكم. يمكن حذفه في أي وقت.",
                     "Из ваших записей будет создан клон вашего голоса в вашем аккаунте. Потом видео смогут говорить вашим голосом. Удалить можно в любой момент."),
        "done": _txt("הקול שלך שובט ✓ מעכשיו הסרטונים יכולים לדבר בקול שלך.",
                     "Your voice is cloned ✓ Videos can now speak in your voice.",
                     "Votre voix est clonée ✓ Les vidéos peuvent maintenant parler avec votre voix.",
                     "تم استنساخ صوتك ✓ يمكن للفيديوهات الآن أن تتكلم بصوتك.",
                     "Голос клонирован ✓ Теперь видео могут говорить вашим голосом."),
    },
    "make_presenter_video": {
        "approver": "client", "client_requestable": True, "internal_only": True, "kind": "execute",
        "does": "Makes ONE video in which the client's video presenter (filmed likeness or AI presenter, "
                "account_state.creative_tools.video_providers) says a script, in their own voice. The video "
                "goes to their Drive for review — nothing is published. Paid add-on: tier, minutes and the "
                "likeness consent are checked when it runs.",
        "params": {"script": {"required": True, "validate": _v_script,
                              "hint": "the exact words the presenter says (3-1500 characters), as the client wrote "
                                      "or approved them"},
                   "voice": {"required": True, "validate": _v_presenter_voice,
                             "hint": "\"clone\" (account_state.voice.voice_clone_exists) or "
                                     "\"recording:<file_id>\" with a file_id from account_state.voice.recordings"},
                   "provider": {"required": False, "validate": _v_presenter,
                                "hint": "heygen | higgsfield — ONLY when the client asked for the other presenter; "
                                        "leave out for their chosen one"}},
        "available": _presenter_video_possible,
        "run": _run_make_presenter_video,
        "display": _presenter_display,
        "details": lambda client_id, params: {"description": params.get("script", "")},
        "slow": {"minutes": 30, "eta": _txt(
            "סרטון כזה לוקח בדרך כלל 5 עד 15 דקות.", "A video like this usually takes 5 to 15 minutes.",
            "Une vidéo comme celle-ci prend généralement 5 à 15 minutes.",
            "فيديو كهذا يستغرق عادة من 5 إلى 15 دقيقة.", "Такое видео обычно делается 5–15 минут.")},
        "title": _txt("סרטון עם הדמות שלך", "A video with your presenter", "Une vidéo avec votre présentateur",
                      "فيديو مع شخصيتك", "Видео с вашим ведущим"),
        "what": _txt("ייווצר סרטון שבו {presenter} אומר/ת את הטקסט, בקול: {voice}. הסרטון יישמר בתיקיית ה-Drive שלכם לבדיקה — שום דבר לא מתפרסם.",
                     "A video will be made in which {presenter} says the script, in this voice: {voice}. It is saved to your Drive folder for review — nothing is published.",
                     "Une vidéo sera créée où {presenter} dit le texte, avec cette voix : {voice}. Elle est enregistrée dans votre Drive pour vérification — rien n'est publié.",
                     "سيُنشأ فيديو يقول فيه {presenter} النص، بهذا الصوت: {voice}. يُحفظ في مجلد Drive للمراجعة — لا يُنشر شيء.",
                     "Будет создано видео, где {presenter} произносит текст голосом: {voice}. Оно сохранится в вашей папке Drive для проверки — ничего не публикуется."),
        "done": _txt("הסרטון מוכן ✓ הוא מחכה לבדיקה שלכם בתיקיית ה-Drive.",
                     "The video is ready ✓ It's waiting for your review in your Drive folder.",
                     "La vidéo est prête ✓ Elle vous attend dans votre dossier Drive.",
                     "الفيديو جاهز ✓ بانتظار مراجعتكم في مجلد Drive.",
                     "Видео готово ✓ Оно ждёт вашей проверки в папке Drive."),
        "done_message": lambda result, lang: (
            _pick(ACTIONS["make_presenter_video"]["done"], lang)
            + (f"\n{result['link']}" if result.get("link") else "")),
    },
    "approve_suggestion": {
        "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
        "does": "Approves ONE of the client's own pending suggestions (approvals.waiting_for_client) — "
                "the same as their approve tap in the dashboard; approving is what lets the team start.",
        "params": {"suggestion_id": {"required": True, "validate": _v_pending_suggestion,
                                     "hint": "suggestion_id copied exactly from approvals.waiting_for_client"}},
        "available": _has_pending_suggestions,
        "run": _run_approve_suggestion,
        "title": _txt("אישור הצעה", "Approve a suggestion", "Approuver une suggestion", "الموافقة على اقتراح",
                      "Одобрить предложение"),
        "what": _txt("ההצעה \"{title}\" תאושר, והצוות יתחיל לעבוד עליה.",
                     "\"{title}\" will be approved and the team starts working on it.",
                     "« {title} » sera approuvée et l'équipe s'y met.",
                     "سيتم اعتماد \"{title}\" ويبدأ الفريق العمل عليها.",
                     "«{title}» будет одобрено, и команда приступит к работе."),
        "done": _txt("אושר ✓ הצוות מתחיל לעבוד על זה.", "Approved ✓ The team is on it.",
                     "Approuvé ✓ L'équipe s'en occupe.", "تمت الموافقة ✓ الفريق يعمل على ذلك.",
                     "Одобрено ✓ Команда приступает."),
    },
    "publish_youtube_from_media": {
        # The client's own click is the approval: the upload lands PRIVATE, so
        # nothing becomes public without a second, deliberate step in YouTube
        # Studio. Daily cap: youtube_content_agent.DAILY_UPLOADS_PER_CLIENT.
        "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
        "does": "Uploads ONE video from the client's Drive media folder (account_state.media_folder.videos, "
                "uploadable=true) to their YouTube channel as PRIVATE — never public or unlisted; they "
                "publish it themselves in YouTube Studio when they like. The system writes its title, "
                "description (with links to their own pages), hashtags and tags for the card — you do "
                "not. Max 3 uploads per client per day.",
        # What the CHAT MODEL may send. The stored proposal is different: the
        # metadata pipeline (agents/youtube_metadata) turns these into the
        # exact title/description/tags the client approves — see "prepare".
        "params": {"drive_file_id": {"required": True, "validate": _v_media_video,
                                     "hint": "drive_file_id copied exactly from account_state.media_folder.videos "
                                             "(never shown to the client)"},
                   "title": {"required": False, "validate": _v_title,
                             "hint": "ONLY a title the client dictated; otherwise leave it out"},
                   "about": {"required": False, "validate": lambda v, cid: _v_short_text(v, 300),
                             "hint": "what the video shows, in the client's own words, if they said; else leave out"},
                   "change_request": {"required": False, "validate": lambda v, cid: _v_short_text(v, 400),
                                      "hint": "on an OPEN card for this video: the client's change "
                                              "(\"shorter description\", \"add this phrase\") copied in their words"}},
        "available": _youtube_upload_possible,
        "prepare": lambda client_id, params, context, rows: _prepare_publish(client_id, params, context, rows),
        "validate_stored": lambda client_id, params: _validate_publish_stored(client_id, params),
        "details": lambda client_id, params: _publish_details(client_id, params),
        "run": _run_publish_youtube_from_media,
        "display": lambda client_id, params: {"file": _media_name(client_id, params.get("drive_file_id", ""))
                                              or "—", "title": params.get("title") or ""},
        "title": _txt("העלאת סרטון ליוטיוב (פרטי)", "Upload a video to YouTube (private)",
                      "Mettre une vidéo sur YouTube (privée)", "رفع فيديو إلى YouTube (خاص)",
                      "Загрузить видео на YouTube (приватно)"),
        "what": _txt("הסרטון \"{file}\" מתיקיית המדיה שלכם יעלה לערוץ היוטיוב שלכם כפרטי — אף אחד לא יראה אותו עד שתפרסמו אותו בעצמכם ב-YouTube Studio.",
                     "\"{file}\" from your media folder will be uploaded to your YouTube channel as private — nobody sees it until you publish it yourself in YouTube Studio.",
                     "« {file} » de votre dossier média sera mise en ligne sur votre chaîne YouTube en privé — personne ne la voit avant que vous la publiiez dans YouTube Studio.",
                     "سيتم رفع \"{file}\" من مجلد الوسائط إلى قناتكم على YouTube كفيديو خاص — لن يراه أحد حتى تنشروه بأنفسكم في YouTube Studio.",
                     "«{file}» из вашей папки медиа будет загружено на ваш канал YouTube как приватное — никто его не увидит, пока вы сами не опубликуете его в YouTube Studio."),
        "done": _txt("הסרטון עלה ליוטיוב כפרטי ✓ אפשר לפרסם אותו מתי שנוח לכם.",
                     "Uploaded to YouTube as private ✓ Publish it whenever you like.",
                     "Mise en ligne sur YouTube en privé ✓ Publiez-la quand vous voulez.",
                     "تم الرفع إلى YouTube كفيديو خاص ✓ انشروه متى شئتم.",
                     "Загружено на YouTube как приватное ✓ Опубликуйте, когда будет удобно."),
        "done_message": lambda result, lang: (
            _pick(ACTIONS["publish_youtube_from_media"]["done"], lang)
            + (f"\n{result['studio_url']}" if result.get("studio_url") else "")),
        "refusals": {"daily_cap": _txt(
            "הגעתם למכסת ההעלאות ליוטיוב להיום (3 ביום). אפשר להעלות שוב מחר — שום דבר לא השתנה.",
            "You've reached today's YouTube upload limit (3 a day). You can upload again tomorrow — nothing was changed.",
            "Vous avez atteint la limite de mises en ligne YouTube du jour (3 par jour). Réessayez demain — rien n'a été modifié.",
            "وصلتم إلى حد الرفع إلى YouTube لهذا اليوم (3 يوميًا). يمكنكم الرفع مجددًا غدًا — لم يتغير شيء.",
            "Достигнут дневной лимит загрузок на YouTube (3 в день). Загрузить снова можно завтра — ничего не изменено.")},
    },
    "send_tiktok_to_inbox": {
        # The client's own click is the approval: the video lands in THEIR
        # TikTok inbox as a draft (Upload-to-Inbox, scope video.upload) and
        # nothing is public until they publish it inside TikTok themselves.
        # No Direct Post, no caption, no privacy setting exists on this path.
        "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
        "does": "Sends ONE video from the client's Drive media folder (account_state.media_folder.videos, "
                "tiktok_uploadable=true) to THEIR OWN TikTok inbox as a draft. Nothing is posted: they "
                "open TikTok, add their caption and publish it themselves. If TikTok isn't connected or "
                "the connection needs renewing, proposing this shows the reconnect button instead.",
        "params": {"drive_file_id": {"required": True, "validate": _v_tiktok_video,
                                     "hint": "drive_file_id copied exactly from account_state.media_folder.videos "
                                             "(tiktok_uploadable=true; never shown to the client)"}},
        "available": _tiktok_send_possible,
        "prepare": lambda client_id, params, context, rows: _prepare_tiktok(client_id, params, context, rows),
        "validate_stored": lambda client_id, params: _validate_tiktok_stored(client_id, params),
        "details": lambda client_id, params: {"file": _media_name(client_id, params.get("drive_file_id", ""))},
        "run": _run_send_tiktok_to_inbox,
        "display": lambda client_id, params: {"file": _media_name(client_id, params.get("drive_file_id", ""))
                                              or "—"},
        "title": _txt("שליחת סרטון לטיוטות בטיקטוק", "Send a video to your TikTok drafts",
                      "Envoyer une vidéo dans vos brouillons TikTok", "إرسال فيديو إلى مسودات تيك توك",
                      "Отправить видео в черновики TikTok"),
        "what": _txt("הסרטון \"{file}\" מתיקיית המדיה שלכם יישלח לתיבת הנכנסים בחשבון הטיקטוק שלכם כטיוטה. שום דבר לא מתפרסם — אתם מוסיפים כיתוב ומפרסמים בעצמכם באפליקציה.",
                     "\"{file}\" from your media folder will be sent to your TikTok inbox as a draft. Nothing is posted — you add the caption and publish it yourself in the app.",
                     "« {file} » de votre dossier média sera envoyée dans votre boîte TikTok en brouillon. Rien n'est publié — vous ajoutez la légende et publiez vous-même dans l'application.",
                     "سيُرسل \"{file}\" من مجلد الوسائط إلى صندوق الوارد في حساب تيك توك كمسودة. لا يُنشر شيء — تضيفون النص وتنشرون بأنفسكم في التطبيق.",
                     "«{file}» из вашей папки медиа будет отправлено во входящие TikTok как черновик. Ничего не публикуется — подпись и публикацию вы делаете сами в приложении."),
        "done": _txt("הסרטון \"{file}\" נשלח לטיוטות שלכם בטיקטוק ✓\nעכשיו פתחו את אפליקציית טיקטוק — מחכה לכם התראה על סרטון בתיבת הנכנסים. שם מוסיפים כיתוב ולוחצים 'פרסם' מתי שנוח לכם. אנחנו לא מפרסמים בשמכם.",
                     "\"{file}\" was sent to your TikTok drafts ✓\nNow open the TikTok app — a notification about the video is waiting in your inbox. Add your caption there and tap Post whenever you like. We never post for you.",
                     "« {file} » a été envoyée dans vos brouillons TikTok ✓\nOuvrez maintenant l'application TikTok — une notification vous attend dans la boîte de réception. Ajoutez votre légende et publiez quand vous voulez. Nous ne publions jamais à votre place.",
                     "تم إرسال \"{file}\" إلى مسودات تيك توك ✓\nافتحوا الآن تطبيق تيك توك — بانتظاركم إشعار عن الفيديو في صندوق الوارد. أضيفوا النص وانشروا متى شئتم. نحن لا ننشر نيابة عنكم.",
                     "«{file}» отправлено в черновики TikTok ✓\nТеперь откройте приложение TikTok — во входящих вас ждёт уведомление о видео. Добавьте подпись и опубликуйте, когда удобно. Мы никогда не публикуем за вас."),
        "done_message": lambda result, lang: _pick(ACTIONS["send_tiktok_to_inbox"]["done"], lang)
            .replace("{file}", result.get("source_file") or "—"),
        "refusals": {
            "needs_reconnect": _txt(
                "החיבור לטיקטוק צריך חידוש לפני שאפשר לשלוח סרטונים — שום דבר לא נשלח. לחצו על הכפתור כדי לחבר מחדש (פעם אחת, דרך טיקטוק).",
                "Your TikTok connection needs renewing before videos can be sent — nothing was sent. Tap the button to reconnect (once, through TikTok).",
                "Votre connexion TikTok doit être renouvelée avant d'envoyer des vidéos — rien n'a été envoyé. Touchez le bouton pour reconnecter (une fois, via TikTok).",
                "يجب تجديد الربط مع تيك توك قبل إرسال الفيديوهات — لم يُرسل شيء. اضغطوا على الزر لإعادة الربط (مرة واحدة، عبر تيك توك).",
                "Подключение к TikTok нужно обновить, прежде чем отправлять видео — ничего не отправлено. Нажмите кнопку, чтобы переподключить (один раз, через TikTok)."),
            "pending_limit": _txt(
                "בתיבת הנכנסים שלכם בטיקטוק כבר מחכים כמה סרטונים שעוד לא פורסמו, וטיקטוק לא מקבלת עוד כרגע. פרסמו או מחקו חלק מהם באפליקציה, ואז אפשר לשלוח שוב — שום דבר לא נשלח.",
                "Several unpublished videos are already waiting in your TikTok inbox, and TikTok won't take more right now. Publish or delete some of them in the app, then you can send again — nothing was sent.",
                "Plusieurs vidéos non publiées attendent déjà dans votre boîte TikTok, et TikTok n'en accepte pas d'autres pour l'instant. Publiez-en ou supprimez-en dans l'application, puis renvoyez — rien n'a été envoyé.",
                "توجد عدة فيديوهات غير منشورة في صندوق الوارد في تيك توك، ولا يقبل تيك توك المزيد حاليًا. انشروا بعضها أو احذفوه في التطبيق ثم أرسلوا مجددًا — لم يُرسل شيء.",
                "Во входящих TikTok уже ждут несколько неопубликованных видео, и сейчас TikTok больше не принимает. Опубликуйте или удалите часть из них в приложении и отправьте снова — ничего не отправлено."),
            "too_large": _txt(
                "הסרטון גדול מדי לשליחה לטיקטוק (עד 64MB) — שום דבר לא נשלח. אפשר לבחור גרסה קצרה או דחוסה יותר מתיקיית המדיה.",
                "This video is too large to send to TikTok (up to 64 MB) — nothing was sent. Pick a shorter or more compressed version from your media folder.",
                "Cette vidéo est trop lourde pour TikTok (64 Mo max) — rien n'a été envoyé. Choisissez une version plus courte ou plus compressée.",
                "الفيديو كبير جدًا للإرسال إلى تيك توك (حتى 64MB) — لم يُرسل شيء. اختاروا نسخة أقصر أو مضغوطة أكثر.",
                "Видео слишком большое для TikTok (до 64 МБ) — ничего не отправлено. Выберите более короткую или сжатую версию."),
        },
        # After an explained refusal, the card that fixes it (shown right away)
        "follow_up": {"needs_reconnect": "connect_tiktok"},
    },
    # ── Google verification ROUND 2 (docs/GOOGLE-ROUND2.md) — every entry
    # below is unavailable while its flag is off (core/round2_flags) ──
    "connect_gmail_send": {
        "approver": "client", "client_requestable": True, "internal_only": False, "kind": "link",
        "link": "/api/oauth/gmail/start",
        "does": "Opens Google's approval screen so the client's own Gmail can SEND the outreach e-mails "
                "they approve one by one (send only — the mailbox is never read).",
        "params": {},
        "available": _gmail_connect_needed,
        "title": _txt("חיבור Gmail לשליחת פניות", "Connect Gmail to send outreach",
                      "Connecter Gmail pour envoyer vos prises de contact", "ربط Gmail لإرسال رسائل التواصل",
                      "Подключить Gmail для отправки писем"),
        "what": _txt("ייפתח מסך האישור של Google. נבקש הרשאת שליחה בלבד — לא נקרא אף מייל בתיבה שלכם. כל מייל יישלח רק אחרי שתאשרו אותו כאן.",
                     "Google's approval screen opens. We ask for permission to send only — we never read your mailbox. Each e-mail is sent only after you approve it here.",
                     "L'écran Google s'ouvre. Nous demandons uniquement l'envoi — nous ne lisons jamais votre boîte. Chaque e-mail part seulement après votre accord ici.",
                     "ستفتح شاشة موافقة Google. نطلب صلاحية الإرسال فقط — لا نقرأ بريدكم أبدًا. كل رسالة تُرسل فقط بعد موافقتكم هنا.",
                     "Откроется окно Google. Мы просим только право отправки — почту мы не читаем. Каждое письмо уходит только после вашего подтверждения здесь."),
        "done": _txt("פותח את מסך האישור של Google…", "Opening Google's approval screen…",
                     "Ouverture de l'écran Google…", "جارٍ فتح شاشة موافقة Google…", "Открываю окно Google…"),
    },
    "send_outreach_email": {
        # The client approves EACH e-mail (one card = one message, no bulk):
        # it goes from their own Gmail, to one business, with the opt-out line.
        "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
        "does": "Sends ONE outreach e-mail from the client's own Gmail to ONE business. You write the draft "
                "for that specific business: its name, one concrete detail from its own website (hook), "
                "and why it fits the client — never a generic text. The system reads the business's site, "
                "checks the address is published there, adds the opt-out line and shows the exact e-mail "
                "on the card. Max 10 a day; one e-mail per business, ever.",
        "params": {"to_email": {"required": True, "validate": lambda v, cid: _v_short_text(v, 254),
                                "hint": "the address exactly as published on the business's own site"},
                   "business_name": {"required": True, "validate": lambda v, cid: _v_short_text(v, 120),
                                     "hint": "the business's name, as it writes it"},
                   "business_url": {"required": True, "validate": lambda v, cid: _v_short_text(v, 300),
                                    "hint": "the business's own website (https://…)"},
                   "hook": {"required": True, "validate": lambda v, cid: _v_short_text(v, 200),
                            "hint": "one concrete detail copied from their site, also used in the body"},
                   "subject": {"required": True, "validate": lambda v, cid: _v_short_text(v, 120),
                               "hint": "short, honest subject"},
                   "body": {"required": True, "validate": lambda v, cid: str(v or "").strip()[:1500],
                            "hint": "the e-mail text: names the business, uses the hook, asks if they'd like details"}},
        "available": _outreach_drafts_possible,
        "approve_blocked": _outreach_send_blocked,
        "prepare": lambda client_id, params, context, rows: _prepare_outreach(client_id, params, context, rows),
        "validate_stored": lambda client_id, params: _validate_outreach_stored(client_id, params),
        "details": lambda client_id, params: {"to": params.get("to_email", ""),
                                              "business": params.get("business_name", ""),
                                              "subject": params.get("subject", ""),
                                              "body": params.get("body", ""),
                                              "source": params.get("source_url", "")},
        "run": _run_send_outreach_email,
        "display": lambda client_id, params: {"business": params.get("business_name") or "—",
                                              "to": params.get("to_email") or "—"},
        "title": _txt("שליחת מייל פנייה", "Send an outreach e-mail", "Envoyer un e-mail de prise de contact",
                      "إرسال رسالة تواصل", "Отправить письмо"),
        "what": _txt("המייל למטה יישלח מה-Gmail שלכם אל {business} ({to}) — רק המייל הזה, פעם אחת. בסופו שורת הסרה מרשימת התפוצה.",
                     "The e-mail below will be sent from your Gmail to {business} ({to}) — only this one, once. It ends with an opt-out line.",
                     "L'e-mail ci-dessous partira de votre Gmail vers {business} ({to}) — uniquement celui-ci, une fois. Il se termine par une ligne de désinscription.",
                     "الرسالة أدناه ستُرسل من Gmail الخاص بكم إلى {business} ({to}) — هذه فقط ومرة واحدة. في نهايتها سطر لإلغاء الاشتراك.",
                     "Письмо ниже уйдёт с вашего Gmail в {business} ({to}) — только оно и один раз. В конце — строка для отказа от писем."),
        "done": _txt("המייל נשלח אל {business} ✓ תשובה, אם תגיע, תגיע לתיבת ה-Gmail שלכם.",
                     "The e-mail to {business} was sent ✓ Any reply arrives in your own Gmail inbox.",
                     "L'e-mail à {business} est parti ✓ Une réponse éventuelle arrivera dans votre Gmail.",
                     "تم إرسال الرسالة إلى {business} ✓ أي رد سيصل إلى بريد Gmail الخاص بكم.",
                     "Письмо в {business} отправлено ✓ Ответ придёт в ваш Gmail."),
        "done_message": lambda result, lang: _pick(ACTIONS["send_outreach_email"]["done"], lang)
            .replace("{business}", result.get("business_name") or "—"),
        "refusals": {},          # filled below (_OUTREACH_SEND_REFUSALS)
        "follow_up": {"needs_reconnect": "connect_gmail_send"},
    },
    "send_outreach_followup": {
        # ONE follow-up per business, ever, 7+ days after the first e-mail with
        # no reply / removal / bounce marked (outreach_policy.followup_refusal).
        # Proposed by the agent, never automatic; the client approves it.
        "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
        "does": "Sends the ONE follow-up e-mail to a business that got the first e-mail 7+ days ago and has not "
                "answered (only businesses listed in account_state.outreach.followup_due). Short and polite: "
                "names the business, refers to the first e-mail, no pressure. The system adds the opt-out line. "
                "Never a second follow-up; never to a business that replied or asked to be removed.",
        "params": {"to_email": {"required": True, "validate": lambda v, cid: _v_short_text(v, 254),
                                "hint": "the address from outreach.followup_due"},
                   "subject": {"required": False, "validate": lambda v, cid: _v_short_text(v, 120),
                               "hint": "optional; default: 'בהמשך לפנייה: <first subject>'"},
                   "body": {"required": True, "validate": lambda v, cid: str(v or "").strip()[:1500],
                            "hint": "the follow-up text: names the business, refers to the first e-mail"}},
        "available": _followup_possible,
        "approve_blocked": _outreach_send_blocked,
        "prepare": lambda client_id, params, context, rows: _prepare_followup(client_id, params, context, rows),
        "validate_stored": lambda client_id, params: _validate_outreach_stored(client_id, params),
        "details": lambda client_id, params: {"to": params.get("to_email", ""),
                                              "business": params.get("business_name", ""),
                                              "subject": params.get("subject", ""),
                                              "body": params.get("body", ""),
                                              "source": params.get("source_url", "")},
        "run": _run_send_outreach_followup,
        "display": lambda client_id, params: {"business": params.get("business_name") or "—",
                                              "to": params.get("to_email") or "—"},
        "title": _txt("מייל מעקב (אחד ויחיד)", "Follow-up e-mail (the only one)", "E-mail de relance (unique)",
                      "رسالة متابعة (واحدة فقط)", "Повторное письмо (единственное)"),
        "what": _txt("{business} ({to}) לא ענו כבר שבוע. המייל למטה הוא מעקב אחד ויחיד — לא נשלח עוד אחד אחריו. בסופו שורת הסרה.",
                     "{business} ({to}) hasn't answered for a week. The e-mail below is the one and only follow-up — none after it. It ends with an opt-out line.",
                     "{business} ({to}) n'a pas répondu depuis une semaine. L'e-mail ci-dessous est l'unique relance. Il se termine par une ligne de désinscription.",
                     "{business} ({to}) لم يردّوا منذ أسبوع. الرسالة أدناه هي المتابعة الوحيدة — لن تتبعها أخرى. في نهايتها سطر إلغاء الاشتراك.",
                     "{business} ({to}) не отвечает неделю. Письмо ниже — единственное напоминание, других не будет. В конце — строка для отказа."),
        "done": _txt("מייל המעקב אל {business} נשלח ✓ זה היה המעקב היחיד — לא נשלח עוד.",
                     "The follow-up to {business} was sent ✓ That was the only one — nothing more will be sent.",
                     "La relance à {business} est partie ✓ C'était la seule.",
                     "تم إرسال المتابعة إلى {business} ✓ كانت الوحيدة.",
                     "Напоминание для {business} отправлено ✓ Оно было единственным."),
        "done_message": lambda result, lang: _pick(ACTIONS["send_outreach_followup"]["done"], lang)
            .replace("{business}", result.get("business_name") or "—"),
        "refusals": {},          # filled below (_OUTREACH_SEND_REFUSALS)
        "follow_up": {"needs_reconnect": "connect_gmail_send"},
    },
    "connect_search_console": {
        "approver": "client", "client_requestable": True, "internal_only": False, "kind": "link",
        "link": "/api/oauth/search-console/start",
        "does": "Opens Google's approval screen for READ-ONLY access to the client's Google Search Console, "
                "so the organic section shows their real keyword positions, clicks and impressions.",
        "params": {},
        "available": _search_console_connect_needed,
        "title": _txt("חיבור Google Search Console (קריאה בלבד)", "Connect Google Search Console (read only)",
                      "Connecter Google Search Console (lecture seule)", "ربط Google Search Console (قراءة فقط)",
                      "Подключить Google Search Console (только чтение)"),
        "what": _txt("ייפתח מסך האישור של Google. הגישה לקריאה בלבד: נראה את מילות החיפוש, הקליקים והמיקומים של האתר שלכם — ולא נשנה שום דבר.",
                     "Google's approval screen opens. Access is read only: we see your site's search keywords, clicks and positions — and change nothing.",
                     "L'écran Google s'ouvre. Accès en lecture seule : mots-clés, clics et positions de votre site — sans rien modifier.",
                     "ستفتح شاشة موافقة Google. الوصول للقراءة فقط: نرى كلمات البحث والنقرات والمواقع لموقعكم — ولا نغيّر شيئًا.",
                     "Откроется окно Google. Доступ только для чтения: ключевые слова, клики и позиции сайта — ничего не меняем."),
        "done": _txt("פותח את מסך האישור של Google…", "Opening Google's approval screen…",
                     "Ouverture de l'écran Google…", "جارٍ فتح شاشة موافقة Google…", "Открываю окно Google…"),
    },
}

_OUTREACH_DRAFT_REFUSALS = {
    "bad_address": _txt("כתובת המייל לא תקינה, אז לא הכנתי טיוטה.", "That e-mail address isn't valid, so I didn't prepare a draft.",
                        "Cette adresse e-mail n'est pas valide — pas de brouillon.", "عنوان البريد غير صالح، لذلك لم أجهّز مسودة.",
                        "Адрес некорректен — черновик не подготовлен."),
    "suppressed": _txt("העסק הזה ביקש לא לקבל מאיתנו פניות, אז לא נשלח אליו כלום.",
                       "This business asked not to be contacted, so nothing will be sent to it.",
                       "Cette entreprise a demandé à ne pas être contactée — rien ne lui sera envoyé.",
                       "طلب هذا العمل عدم التواصل معه، لذلك لن يُرسل إليه شيء.",
                       "Эта компания просила не писать ей — ничего отправлено не будет."),
    "already_contacted": _txt("כבר פנינו לעסק הזה פעם אחת — לא שולחים פנייה שנייה.",
                              "We've already written to this business once — we don't send a second approach.",
                              "Nous avons déjà écrit à cette entreprise une fois — pas de seconde prise de contact.",
                              "تواصلنا مع هذا العمل مرة من قبل — لا نرسل رسالة ثانية.",
                              "Мы уже писали этой компании — второе письмо не отправляем."),
    "site_unreadable": _txt("לא הצלחתי לקרוא את האתר של העסק, אז אין לי במה לבסס פנייה אישית. אפשר לבדוק את הכתובת ולנסות שוב.",
                            "I couldn't read the business's website, so there's nothing to base a personal message on. Check the address and try again.",
                            "Je n'ai pas pu lire le site de l'entreprise — vérifiez l'adresse et réessayez.",
                            "لم أتمكن من قراءة موقع العمل — تحققوا من العنوان وحاولوا مجددًا.",
                            "Не удалось прочитать сайт компании — проверьте адрес и попробуйте снова."),
    "bad_site": _txt("כתובת האתר של העסק צריכה להתחיל ב-https://.", "The business's website address must start with https://.",
                     "L'adresse du site doit commencer par https://.", "يجب أن يبدأ عنوان الموقع بـ https://.",
                     "Адрес сайта должен начинаться с https://."),
    "address_not_on_site": _txt("כתובת המייל הזו לא מופיעה באתר של העסק. אנחנו כותבים רק לכתובת שהעסק עצמו פרסם — לא מנחשים כתובות.",
                                "That address doesn't appear on the business's own website. We only write to an address the business published itself — never a guessed one.",
                                "Cette adresse n'apparaît pas sur le site de l'entreprise. Nous n'écrivons qu'à une adresse publiée par l'entreprise.",
                                "هذا العنوان غير موجود في موقع العمل. نكتب فقط لعنوان نشره العمل بنفسه.",
                                "Этого адреса нет на сайте компании. Пишем только на адрес, опубликованный самой компанией."),
    "not_personal": _txt("הטיוטה עוד לא אישית מספיק: היא צריכה לציין את שם העסק ופרט מסוים שבאמת מופיע באתר שלו, ולא להיות דומה לפנייה קודמת. אכתוב גרסה חדשה.",
                         "The draft isn't personal enough yet: it must name the business and use a specific detail that really appears on its site, and not repeat an earlier message. I'll write a new version.",
                         "Le brouillon n'est pas assez personnel : il doit nommer l'entreprise et citer un détail réel de son site. Je réécris.",
                         "المسودة ليست شخصية بما يكفي: يجب أن تذكر اسم العمل وتفصيلًا حقيقيًا من موقعه. سأكتب نسخة جديدة.",
                         "Черновик недостаточно личный: нужно название компании и конкретная деталь с её сайта. Перепишу."),
}

_OUTREACH_SEND_REFUSALS = {
    "daily_cap": _txt("הגעתם למכסת המיילים להיום (עד 10 ביום). המייל לא נשלח — אפשר לאשר אותו שוב מחר.",
                      "You've reached today's e-mail limit (up to 10 a day). It wasn't sent — you can approve it again tomorrow.",
                      "Limite d'e-mails du jour atteinte (10 par jour). Rien n'est parti — réessayez demain.",
                      "وصلتم إلى حد الرسائل اليومي (حتى 10). لم تُرسل — يمكن الموافقة مجددًا غدًا.",
                      "Дневной лимит писем исчерпан (до 10). Не отправлено — повторите завтра."),
    "budget": _txt("תקציב סוכן ה-Gmail לחודש הזה נוצל, אז המייל לא נשלח. הוא מתחדש בתחילת החודש.",
                   "This month's Gmail agent budget is used up, so the e-mail wasn't sent. It renews at the start of the month.",
                   "Le budget mensuel de l'agent Gmail est épuisé — rien n'est parti. Il se renouvelle en début de mois.",
                   "ميزانية وكيل Gmail لهذا الشهر نفدت، لذلك لم تُرسل الرسالة. تتجدد في بداية الشهر.",
                   "Месячный бюджет Gmail-агента исчерпан — письмо не отправлено. Он обновится в начале месяца."),
    "not_entitled": _txt("שליחת מיילים כלולה בחבילת סוכן ה-Gmail, שאינה בחבילה שלכם — המייל לא נשלח.",
                         "Sending e-mails is part of the Gmail agent package, which isn't in your plan — nothing was sent.",
                         "L'envoi d'e-mails fait partie du forfait agent Gmail, absent de votre offre — rien n'est parti.",
                         "إرسال الرسائل جزء من باقة وكيل Gmail غير المشمولة في خطتكم — لم يُرسل شيء.",
                         "Отправка писем входит в пакет Gmail-агента, которого нет в вашем тарифе — ничего не отправлено."),
    "suppressed": _OUTREACH_DRAFT_REFUSALS["suppressed"],
    "already_contacted": _OUTREACH_DRAFT_REFUSALS["already_contacted"],
    "needs_reconnect": _txt("החיבור ל-Gmail צריך חידוש — המייל לא נשלח. לחצו על הכפתור כדי לחבר מחדש, ואז אשרו שוב.",
                            "Your Gmail connection needs renewing — the e-mail wasn't sent. Tap the button to reconnect, then approve again.",
                            "Votre connexion Gmail doit être renouvelée — rien n'est parti. Reconnectez puis validez à nouveau.",
                            "يجب تجديد الربط مع Gmail — لم تُرسل الرسالة. أعيدوا الربط ثم وافقوا مجددًا.",
                            "Подключение Gmail нужно обновить — письмо не отправлено. Переподключите и подтвердите снова."),
    "send_disabled": _txt("שליחת מיילים עדיין לא פעילה במערכת — שום דבר לא נשלח.",
                          "E-mail sending isn't switched on yet — nothing was sent.",
                          "L'envoi d'e-mails n'est pas encore activé — rien n'est parti.",
                          "إرسال الرسائل غير مفعّل بعد — لم يُرسل شيء.",
                          "Отправка писем ещё не включена — ничего не отправлено."),
}
_OUTREACH_SEND_REFUSALS["followup_not_allowed"] = _txt(
    "אי אפשר לשלוח את המעקב: או שכבר נשלח מעקב לעסק הזה, או שהם כבר ענו / ביקשו להסיר / המייל חזר, או שעוד לא עבר שבוע. לא נשלח כלום.",
    "The follow-up can't go: one was already sent, or they replied / asked to be removed / it bounced, or a week hasn't passed. Nothing was sent.",
    "La relance ne peut pas partir (déjà envoyée, réponse, désinscription, rejet, ou moins d'une semaine). Rien n'est parti.",
    "لا يمكن إرسال المتابعة (أُرسلت من قبل، أو ردّوا/طلبوا الإزالة/ارتدّت، أو لم يمر أسبوع). لم يُرسل شيء.",
    "Напоминание отправить нельзя (уже было, ответили/просили не писать/письмо вернулось, или не прошла неделя). Ничего не отправлено.")
_OUTREACH_SEND_REFUSALS["no_opt_out"] = _txt(
    "במייל חסרה שורת ההסרה מרשימת התפוצה, אז הוא לא נשלח.", "The opt-out line is missing, so it wasn't sent.",
    "La ligne de désinscription manque — rien n'est parti.", "سطر إلغاء الاشتراك مفقود، لذلك لم تُرسل.",
    "Нет строки для отказа от писем — не отправлено.")
_OUTREACH_SEND_REFUSAL_CODES = set(_OUTREACH_SEND_REFUSALS)
ACTIONS["send_outreach_email"]["refusals"] = _OUTREACH_SEND_REFUSALS
ACTIONS["send_outreach_followup"]["refusals"] = _OUTREACH_SEND_REFUSALS

# Explained creative refusals (no alert — nothing is broken). Anything else,
# a provider's own failure included, is _FAILED + an admin alert: the vendor's
# words never reach the client.
_CREATIVE_REFUSALS = {
    "missing_tool": _txt(
        "כדי לעשות את זה צריך קודם לחבר כלי שעוד לא מחובר — שום דבר לא נוצר. הכרטיס שלו מחכה במסך הבית, עם הדרכה צעד אחר צעד, ואם נתקעים יש שם כפתור ונעשה את זה יחד.",
        "A tool needed for this isn't connected yet — nothing was made. Its card is waiting on the home screen with a step-by-step guide, and if you get stuck there's a button to do it together.",
        "Un outil nécessaire n'est pas encore connecté — rien n'a été créé. Sa carte vous attend sur l'accueil avec un guide pas à pas, et un bouton pour le faire ensemble si besoin.",
        "أداة مطلوبة لذلك غير مرتبطة بعد — لم يُنشأ شيء. بطاقتها بانتظاركم في الشاشة الرئيسية مع إرشاد خطوة بخطوة، وزر لنفعل ذلك معًا إن علقتم.",
        "Нужный инструмент ещё не подключён — ничего не создано. Его карточка ждёт на главной с пошаговой инструкцией, а если застрянете — там есть кнопка, сделаем вместе."),
    "not_in_plan": _txt(
        "סרטוני דמות הם תוספת בתשלום, ואין כרגע דקות זמינות בחבילה שלכם — שום דבר לא נוצר. אפשר לדבר איתנו על זה כאן בצ׳אט.",
        "Presenter videos are a paid add-on, and your plan has no minutes available right now — nothing was made. You can talk to us about it here in the chat.",
        "Les vidéos avec présentateur sont une option payante, et votre formule n'a pas de minutes disponibles — rien n'a été créé. Parlons-en ici dans le chat.",
        "فيديوهات الشخصية إضافة مدفوعة، ولا توجد دقائق متاحة في باقتكم حاليًا — لم يُنشأ شيء. يمكن التحدث معنا عن ذلك هنا في الدردشة.",
        "Видео с ведущим — платное дополнение, и сейчас в вашем тарифе нет доступных минут — ничего не создано. Можно обсудить это с нами здесь в чате."),
    "not_ready_yet": _txt(
        "עוד לא הכל מוכן לזה (למשל הדמות עוד בהכנה, חסרה הסכמה בכרטיס, או שאין עדיין הקלטה מתאימה) — שום דבר לא נוצר. אפשר לשאול אותי כאן מה בדיוק חסר.",
        "Not everything is ready for this yet (for example the presenter is still being prepared, a consent is missing on its card, or there's no suitable recording yet) — nothing was made. Ask me here what exactly is missing.",
        "Tout n'est pas encore prêt (présentateur en préparation, consentement manquant sur sa carte, ou pas d'enregistrement adapté) — rien n'a été créé. Demandez-moi ici ce qui manque.",
        "ليس كل شيء جاهزًا بعد (مثلًا الشخصية قيد التجهيز، أو موافقة ناقصة في بطاقتها، أو لا يوجد تسجيل مناسب) — لم يُنشأ شيء. اسألوني هنا عمّا ينقص بالضبط.",
        "Ещё не всё готово (например, ведущий ещё готовится, на карточке не хватает согласия или нет подходящей записи) — ничего не создано. Спросите меня здесь, чего именно не хватает."),
}
ACTIONS["clone_voice"]["refusals"] = _CREATIVE_REFUSALS
ACTIONS["make_presenter_video"]["refusals"] = _CREATIVE_REFUSALS

# Slow actions answer the click at once with a progress card and run in the
# background (decide → _execute_in_background); the card polls and turns into
# the result. "minutes": past twice that, a still-running job is "stalled" —
# the client reads that the team is checking, and the team gets one alert.
ACTIONS["configure_conversion"]["slow"] = {"minutes": 5, "eta": _txt(
    "זה לוקח בדרך כלל פחות מדקה.", "It usually takes under a minute.", "Cela prend généralement moins d'une minute.",
    "يستغرق ذلك عادة أقل من دقيقة.", "Обычно это занимает меньше минуты.")}
ACTIONS["publish_youtube_from_media"]["slow"] = {"minutes": 20, "eta": _txt(
    "העלאה לוקחת בדרך כלל בין דקה לכמה דקות, לפי גודל הסרטון.",
    "An upload usually takes from one to a few minutes, depending on the video's size.",
    "Une mise en ligne prend généralement d'une à quelques minutes, selon la taille de la vidéo.",
    "يستغرق الرفع عادة من دقيقة إلى بضع دقائق حسب حجم الفيديو.",
    "Загрузка обычно занимает от минуты до нескольких минут, в зависимости от размера видео.")}
ACTIONS["send_tiktok_to_inbox"]["slow"] = {"minutes": 10, "eta": _txt(
    "השליחה לוקחת בדרך כלל דקה-שתיים.", "Sending usually takes a minute or two.",
    "L'envoi prend généralement une à deux minutes.", "يستغرق الإرسال عادة دقيقة أو دقيقتين.",
    "Отправка обычно занимает минуту-две.")}

_FOLLOWUP_DRAFT_REFUSALS = {
    **_OUTREACH_DRAFT_REFUSALS,
    "followup_pending": _txt("כבר יש טיוטת מעקב לעסק הזה שמחכה לאישור שלכם.",
                             "There's already a follow-up draft for this business waiting for your approval.",
                             "Une relance pour cette entreprise attend déjà votre accord.",
                             "توجد مسودة متابعة لهذا العمل بانتظار موافقتكم.",
                             "Черновик напоминания для этой компании уже ждёт вашего подтверждения."),
    "too_soon": _txt("עוד לא עבר שבוע מהמייל הראשון — מעקב אפשר רק אחרי 7 ימים בלי תשובה.",
                     "A week hasn't passed since the first e-mail — a follow-up is possible only after 7 days without an answer.",
                     "Moins d'une semaine depuis le premier e-mail — relance possible après 7 jours sans réponse.",
                     "لم يمر أسبوع على الرسالة الأولى — المتابعة ممكنة بعد 7 أيام دون رد.",
                     "С первого письма не прошла неделя — напоминание возможно через 7 дней без ответа."),
    "no_first_message": _txt("לעסק הזה עוד לא נשלח מייל ראשון, אז אין למה לשלוח מעקב.",
                             "No first e-mail went to this business yet, so there's nothing to follow up.",
                             "Aucun premier e-mail n'est parti vers cette entreprise.",
                             "لم تُرسل رسالة أولى لهذا العمل بعد.",
                             "Первое письмо этой компании ещё не отправлялось."),
    "followup_already_sent": _txt("כבר נשלח מעקב לעסק הזה. שולחים מעקב אחד בלבד — לא יותר.",
                                  "A follow-up was already sent to this business. We send one follow-up only — never more.",
                                  "Une relance a déjà été envoyée. Une seule, jamais plus.",
                                  "أُرسلت متابعة لهذا العمل من قبل. متابعة واحدة فقط.",
                                  "Напоминание уже отправлялось. Только одно — не больше."),
    "marked_replied": _txt("סימנתם שהעסק הזה ענה — אז לא שולחים מעקב. ההמשך איתם הוא אצלכם ב-Gmail.",
                           "You marked that this business replied — so no follow-up. The conversation continues in your Gmail.",
                           "Vous avez indiqué une réponse — pas de relance.", "أشرتم إلى أنهم ردّوا — لا متابعة.",
                           "Вы отметили, что ответили — напоминания не будет."),
    "marked_bounced": _txt("המייל לעסק הזה חזר, אז לא שולחים אליו מעקב.",
                           "The e-mail to this business bounced, so no follow-up goes to it.",
                           "L'e-mail a été rejeté — pas de relance.", "ارتدّت الرسالة — لا متابعة.",
                           "Письмо вернулось — напоминания не будет."),
    "record_unavailable": _txt("לא הצלחתי לקרוא את רישום השליחות כרגע, אז לא הכנתי מעקב. נסו שוב בעוד רגע.",
                               "I couldn't read the send record right now, so I didn't prepare a follow-up. Try again shortly.",
                               "Je n'ai pas pu lire l'historique d'envoi — réessayez bientôt.",
                               "تعذّرت قراءة سجل الإرسال — حاولوا بعد قليل.",
                               "Не удалось прочитать историю отправок — попробуйте чуть позже."),
}

# Lines a TikTok send shows INSTEAD of its card when the connection can't
# carry it (_prepare_tiktok) — paired with the connect_tiktok card
_TIKTOK_CONNECT_FIRST = _txt(
    "כדי לשלוח את \"{file}\" לטיוטות בטיקטוק צריך קודם לחבר את חשבון הטיקטוק שלכם. לחצו על הכפתור — זה פעם אחת, דרך טיקטוק, ואז אכין את השליחה.",
    "To send \"{file}\" to your TikTok drafts, your TikTok account needs to be connected first. Tap the button — it's once, through TikTok — and then I'll set up the send.",
    "Pour envoyer « {file} » dans vos brouillons TikTok, connectez d'abord votre compte TikTok. Touchez le bouton — une seule fois, via TikTok — puis je prépare l'envoi.",
    "لإرسال \"{file}\" إلى مسودات تيك توك يجب أولًا ربط حساب تيك توك. اضغطوا على الزر — مرة واحدة عبر تيك توك — ثم أجهّز الإرسال.",
    "Чтобы отправить «{file}» в черновики TikTok, сначала подключите аккаунт TikTok. Нажмите кнопку — один раз, через TikTok, — и я подготовлю отправку.")
_TIKTOK_RECONNECT_FIRST = _txt(
    "החיבור לטיקטוק צריך חידוש לפני שאפשר לשלוח את \"{file}\" לטיוטות — שום דבר לא נשלח. לחצו על הכפתור כדי לחבר מחדש (פעם אחת, דרך טיקטוק), ואז אכין את השליחה.",
    "Your TikTok connection needs renewing before \"{file}\" can go to your drafts — nothing was sent. Tap the button to reconnect (once, through TikTok), and then I'll set up the send.",
    "Votre connexion TikTok doit être renouvelée avant d'envoyer « {file} » — rien n'a été envoyé. Touchez le bouton pour reconnecter (une fois, via TikTok), puis je prépare l'envoi.",
    "يجب تجديد الربط مع تيك توك قبل إرسال \"{file}\" إلى المسودات — لم يُرسل شيء. اضغطوا على الزر لإعادة الربط (مرة واحدة عبر تيك توك)، ثم أجهّز الإرسال.",
    "Подключение к TikTok нужно обновить, прежде чем отправить «{file}» в черновики — ничего не отправлено. Нажмите кнопку, чтобы переподключить (один раз, через TikTok), и я подготовлю отправку.")

# Fixed lines for every other outcome (code decides, the model never writes them)
_STAFF_REQUEST = _txt(
    "שלחתי לצוות לאישור — מכיוון שזה משנה משהו באוויר, איש צוות מאשר את זה. אעדכן כאן כשזה בוצע.",
    "I've sent this to the team for approval — since it changes something live, a team member approves it. I'll update you here when it's done.",
    "Je l'ai envoyé à l'équipe pour validation — comme cela modifie quelque chose en ligne, un membre de l'équipe l'approuve. Je vous tiens au courant ici.",
    "أرسلت ذلك إلى الفريق للموافقة — لأنه يغيّر شيئًا مباشرًا، يوافق عليه أحد أعضاء الفريق. سأبلغكم هنا عند التنفيذ.",
    "Я отправил это команде на подтверждение — это меняет что-то в рабочей системе, поэтому подтверждает сотрудник. Сообщу здесь, когда будет готово.")
_CANCELLED = _txt("בוטל — לא בוצע שום שינוי.", "Cancelled — nothing was changed.", "Annulé — rien n'a été modifié.",
                  "أُلغي — لم يتغير شيء.", "Отменено — ничего не изменено.")
_FAILED = _txt("זה לא הצליח הפעם, ושום דבר לא השתנה. הצוות קיבל את הפרטים ומטפל בזה.",
               "That didn't go through this time, and nothing was changed. The team has the details and is on it.",
               "Cela n'a pas abouti cette fois, et rien n'a été modifié. L'équipe a les détails et s'en occupe.",
               "لم ينجح ذلك هذه المرة ولم يتغير شيء. الفريق لديه التفاصيل ويعالج الأمر.",
               "В этот раз не получилось, ничего не изменено. Команда получила детали и занимается этим.")
# A slow action's progress card: what runs, how long it usually takes, and
# that the client will be told — never a bare spinner
_PROGRESS = _txt(
    "התחלנו ✓ {eta} אין צורך לחכות כאן: אפשר להמשיך לעבוד, והכרטיס הזה יתעדכן (וגם נכתוב כאן בצ׳אט) ברגע שזה מוכן.",
    "Started ✓ {eta} No need to wait here: carry on working — this card updates (and we'll write here in the chat) the moment it's ready.",
    "C'est parti ✓ {eta} Inutile d'attendre ici : continuez à travailler — cette carte se met à jour (et nous écrirons ici) dès que c'est prêt.",
    "بدأنا ✓ {eta} لا حاجة للانتظار هنا: تابعوا عملكم — ستتحدث هذه البطاقة (وسنكتب هنا في الدردشة) فور الجاهزية.",
    "Начали ✓ {eta} Ждать здесь не нужно: продолжайте работать — карточка обновится (и мы напишем в чат), как только всё будет готово.")
_STALLED = _txt(
    "זה לוקח יותר זמן מהרגיל. הצוות קיבל התראה ובודק — נעדכן כאן.",
    "This is taking longer than usual. The team has been alerted and is checking — we'll update you here.",
    "Cela prend plus de temps que d'habitude. L'équipe a été alertée et vérifie — nous vous tiendrons au courant ici.",
    "يستغرق ذلك وقتًا أطول من المعتاد. تم تنبيه الفريق وهو يتحقق — سنبلغكم هنا.",
    "Это занимает больше времени, чем обычно. Команда уведомлена и проверяет — сообщим здесь.")
_EXPIRED = _txt("הבקשה הזו כבר לא בתוקף — כתבו לי שוב ואכין חדשה.", "This request has expired — message me again and I'll set up a new one.",
                "Cette demande a expiré — écrivez-moi et j'en prépare une nouvelle.", "انتهت صلاحية هذا الطلب — راسلوني وسأجهز طلبًا جديدًا.",
                "Срок запроса истёк — напишите мне, и я подготовлю новый.")
NO_CARD = _txt("(את הכפתור לאישור לא הצלחתי להציג כרגע — הצוות קיבל את הבקשה ויטפל בה.)",
               "(I couldn't show the approval button right now — the team has the request and will handle it.)",
               "(Je n'ai pas pu afficher le bouton de validation — l'équipe a la demande et s'en occupe.)",
               "(تعذّر عرض زر الموافقة الآن — الفريق استلم الطلب وسيتولاه.)",
               "(Не удалось показать кнопку подтверждения — команда получила запрос и займётся им.)")


def _lang(client_id: int) -> str:
    try:
        lang = (_client(client_id) or {}).get("language") or "he"
    except Exception:
        lang = "he"
    return lang if lang in LANGS else "he"


def _pick(texts: dict, lang: str) -> str:
    return texts.get(lang) or texts["he"]


# ─── What a chat may offer this turn ─────────────────────────────────────────

def _actor_for(origin: str) -> str:
    return "client" if origin == "client_chat" else "admin"


def _may_propose(spec: dict, actor: str) -> bool:
    if actor == "admin":
        return spec["approver"] == "admin" or spec.get("admin_proposable", False)
    return spec["approver"] == "client" or spec.get("client_requestable", False)


def _approver_for(spec: dict, actor: str) -> str:
    """Who clicks: a Control Room proposal is always approved in the Control
    Room; a client-chat one by its spec's approver."""
    return "admin" if actor == "admin" else spec["approver"]


def _is_internal(client: dict) -> bool:
    return (client or {}).get("billing_exempt") is True


def available(client_id: int, origin: str, state: dict, client: dict = None) -> list:
    """The actions this chat may propose for this client right now, in the
    shape the model sees. Never raises."""
    actor = _actor_for(origin)
    client = client if client is not None else _client(client_id)
    out = []
    for name, spec in ACTIONS.items():
        try:
            if not _may_propose(spec, actor):
                continue
            if spec["internal_only"] and not _is_internal(client):
                continue
            if not spec["available"](state, client):
                continue
        except Exception as e:
            _log(f"client {client_id}: availability check {name} failed: {type(e).__name__}",
                 severity="WARNING", client_id=client_id)
            continue
        out.append({"name": name, "does": spec["does"],
                    "params": {p: d.get("hint", "") for p, d in spec["params"].items()},
                    "approver": "client" if spec["approver"] == "client" else "staff"})
    return out


def validate_params(spec: dict, params, client_id: int) -> dict:
    """Whitelisted keys only, every value through its validator. ValueError
    on anything else — an unknown key is a tampered request, not a typo."""
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    unknown = set(params) - set(spec["params"])
    if unknown:
        raise ValueError(f"unknown params: {sorted(unknown)}")
    clean = {}
    for name, definition in spec["params"].items():
        value = params.get(name)
        if value in (None, ""):
            if definition.get("required"):
                raise ValueError(f"{name} is required")
            continue
        clean[name] = definition["validate"](value, client_id)
    return clean


# ─── Storage ─────────────────────────────────────────────────────────────────

def _get(proposal_id: int) -> dict:
    rows = (_db().table(TABLE).select("*").eq("id", int(proposal_id)).limit(1).execute().data or [])
    return rows[0] if rows else {}


def _claim(proposal_id: int, from_statuses, changes: dict) -> bool:
    """Compare-and-set: moves the row only if it is still in one of
    from_statuses. False = someone else got there first (double click)."""
    rows = (_db().table(TABLE).update(changes).eq("id", int(proposal_id))
            .in_("status", list(from_statuses)).execute().data or [])
    return bool(rows)


def _open_rows(client_id: int, statuses=OPEN_STATUSES) -> list:
    try:
        return (_db().table(TABLE).select("*").eq("client_id", int(client_id))
                .in_("status", list(statuses)).order("created_at", desc=True)
                .limit(20).execute().data or [])
    except Exception as e:
        _log(f"client {client_id}: agent_actions unreadable ({type(e).__name__}) — migration pending?",
             severity="WARNING", client_id=client_id)
        return []


def _expired(row: dict) -> bool:
    try:
        created = datetime.fromisoformat(str(row.get("created_at")).replace("Z", "+00:00"))
    except Exception:
        return True
    return _now() - created > timedelta(hours=PROPOSAL_TTL_HOURS)


def _approve_blocked(spec: dict, client_id) -> str:
    hook = spec.get("approve_blocked")
    if not hook:
        return ""
    try:
        return hook(int(client_id or 0)) or ""
    except Exception as e:
        _log(f"client {client_id}: approve_blocked check failed ({type(e).__name__}) — blocking",
             severity="WARNING", client_id=client_id)
        return "send_disabled"


def _fill(spec: dict, row: dict, text: str) -> str:
    """The card's {placeholders} from OUR reads (e.g. Drive's file name), never the model."""
    params = row.get("params") or {}
    if "{title}" in text:
        text = text.replace("{title}", _suggestion_title(row.get("client_id"), params.get("suggestion_id")))
    if spec.get("display"):
        try:
            for key, value in spec["display"](row.get("client_id"), params).items():
                text = text.replace("{" + key + "}", str(value))
        except Exception:
            pass
    return text


def _since(stamp) -> timedelta:
    try:
        return _now() - datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except Exception:
        return timedelta(0)


def _stalled(row: dict, spec: dict) -> bool:
    slow = spec.get("slow") or {}
    return (row.get("status") == "running" and bool(slow)
            and _since(row.get("decided_at") or row.get("created_at")) > timedelta(minutes=2 * slow["minutes"]))


def progress_text(spec: dict, lang: str) -> str:
    return _pick(_PROGRESS, lang).replace("{eta}", _pick(spec["slow"]["eta"], lang))


def _result_text(spec: dict, row: dict, lang: str) -> str:
    """What a finished card says — the same plain lines decide() posts."""
    if row.get("status") == "done":
        return _fill(spec, row, _pick(spec.get("done") or {"he": ""}, lang))
    if row.get("status") == "failed":
        refusal = (spec.get("refusals") or {}).get(row.get("outcome") or "")
        return _pick(refusal or _FAILED, lang)
    if row.get("status") == "cancelled":
        return _pick(_CANCELLED, lang)
    return ""


def card(row: dict, actor: str, lang: str = "he") -> dict:
    spec = ACTIONS.get(row.get("action")) or {}
    params = row.get("params") or {}
    what = _fill(spec, row, _pick(spec.get("what") or {"he": ""}, lang))
    progress = None
    if row.get("status") == "running" and spec.get("slow"):
        stalled = _stalled(row, spec)
        progress = {"text": _pick(_STALLED, lang) if stalled else progress_text(spec, lang),
                    "started_at": row.get("decided_at"), "stalled": stalled}
    details = None
    if spec.get("details"):
        # Everything the click will send, for the client to read before approving
        try:
            details = spec["details"](row.get("client_id"), params)
        except Exception:
            details = None
    return {"id": row.get("id"), "action": row.get("action"), "status": row.get("status"),
            "persona": row.get("persona", "general"), "origin": row.get("origin"),
            "title": _pick(spec.get("title") or {"he": row.get("action", "")}, lang),
            "what": what, "params": params if actor == "admin" else {}, "details": details,
            "approver": row.get("approver"), "kind": spec.get("kind", "execute"),
            "can_decide": row.get("status") in OPEN_STATUSES and row.get("approver") == actor,
            # The admin "enter as client" view offers its staff button only here
            "staff_may_decide": (row.get("status") in OPEN_STATUSES and row.get("approver") == "client"
                                 and staff_may_decide(spec)),
            # Approve stays off, with this plain reason (e.g. sending not on yet)
            "blocked": (_pick((spec.get("refusals") or {}).get(code) or _FAILED, lang)
                        if row.get("status") in OPEN_STATUSES
                        and (code := _approve_blocked(spec, row.get("client_id"))) else ""),
            # A slow action: the running card's text, then its plain result
            "progress": progress, "result": _result_text(spec, row, lang),
            "created_at": row.get("created_at")}


def _suggestion_title(client_id, suggestion_id) -> str:
    if not suggestion_id:
        return ""
    try:
        from agents.engagement_agent import get_suggestions
        for s in get_suggestions(client_id, status="", limit=50):
            if s.get("id") == suggestion_id:
                return s.get("title", "")
    except Exception:
        pass
    return ""


def open_cards(client_id: int, actor: str, persona: str = None) -> list:
    """Open proposals as cards. A client sees only its own chat's cards (all
    of them, including ones waiting on staff, which carry no buttons); the
    admin sees every open card for the client."""
    lang = _lang(client_id) if actor == "client" else "he"
    out = []
    # A running slow action stays on screen as its progress card after a reload
    for row in _open_rows(client_id, OPEN_STATUSES + ("running",)):
        if actor == "client" and (row.get("origin") != "client_chat"
                                  or (persona and row.get("persona", "general") != persona)):
            continue
        if row.get("status") == "running":
            if not (ACTIONS.get(row.get("action")) or {}).get("slow"):
                continue
        elif _expired(row):
            continue
        out.append(card(row, actor, lang))
    return out


def status_card(proposal_id: int, client_id: int) -> dict:
    """One card, for a progress card's poll — {} when it is not this
    client's. A stalled job raises ONE admin alert (the client's card already
    says the team is checking)."""
    try:
        row = _get(proposal_id)
    except Exception:
        return {}
    if not row or int(row.get("client_id") or 0) != int(client_id or 0) or row.get("origin") != "client_chat":
        return {}
    spec = ACTIONS.get(row.get("action")) or {}
    if _stalled(row, spec):
        try:
            from core import chat_fallback
            chat_fallback.alert_once(SERVICE_NAME, int(client_id), f"stalled:{proposal_id}",
                                     f"client {client_id}: approved '{row.get('action')}' (proposal {proposal_id}) "
                                     f"is still running after {2 * spec['slow']['minutes']} min — check it", days=7)
        except Exception:
            pass
    return card(row, "client", _lang(client_id))


# ─── Propose ─────────────────────────────────────────────────────────────────

def propose(client_id: int, origin: str, persona: str, raw, state: dict = None,
            client: dict = None, dry_run: bool = False, context: dict = None) -> dict:
    """The model's proposed_action -> a stored proposal + card, or
    {"error": code}. Everything is checked here; nothing runs.

    An action with a "prepare" hook (publish_youtube_from_media) turns the
    model's params into the proposal the client approves — and may instead
    answer {"clarify": question} or {"refuse": text}, plain lines the chat
    shows in place of a card. The prepared params are checked by the action's
    "validate_stored" before anything is stored. `context`: {"message",
    "language"} from the chat turn."""
    if not isinstance(raw, dict):
        return {"error": "malformed"}
    name = str(raw.get("name") or "").strip()
    if not name:
        return {}
    spec = ACTIONS.get(name)
    if not spec:
        return {"error": "unknown_action"}
    actor = _actor_for(origin)
    if not _may_propose(spec, actor):
        return {"error": "not_allowed_here"}
    client = client if client is not None else _client(client_id)
    if spec["internal_only"] and not _is_internal(client):
        return {"error": "internal_only"}
    try:
        if state is not None and not spec["available"](state, client):
            return {"error": "not_available"}
        params = validate_params(spec, raw.get("params"), client_id)
    except ValueError as e:
        _log(f"client {client_id}: proposal {name} refused: {e}", client_id=client_id)
        return {"error": "invalid_params"}
    if dry_run:
        return {"dry_run": True, "action": name, "params": params}

    approver = _approver_for(spec, actor)
    status = "proposed" if approver == actor else "awaiting_staff"
    lang = _lang(client_id) if actor == "client" else "he"
    open_rows = [r for r in _open_rows(client_id) if r.get("action") == name and not _expired(r)]
    supersedes = None
    if spec.get("prepare"):
        try:
            prepared = spec["prepare"](client_id, params, {"language": lang, **(context or {})}, open_rows)
        except Exception as e:
            _log(f"client {client_id}: preparing {name} failed: {type(e).__name__}: {str(e)[:300]}",
                 severity="WARNING", client_id=client_id)
            return {"error": "prepare_failed"}
        for answer in ("clarify", "refuse"):
            if prepared.get(answer):
                return {answer: prepared[answer]}
        if prepared.get("switch_to"):
            # The action can't go through as things stand, and another action
            # fixes that (send_tiktok_to_inbox → connect_tiktok): show THAT
            # card, with the prepared line in place of the model's reply.
            # state=None: the fix is offered because of what prepare just
            # learned, which the turn's snapshot may not know yet.
            switched = propose(client_id, origin, persona, {"name": prepared["switch_to"], "params": {}},
                               client=client, context=context)
            if switched.get("card"):
                return {**switched, "notice": prepared.get("notice", "")}
            return {"refuse": prepared.get("notice")} if prepared.get("notice") else switched
        if prepared.get("existing"):
            return {"card": card(prepared["existing"], actor, lang), "status": prepared["existing"].get("status")}
        params, supersedes = prepared["params"], prepared.get("supersedes")
        try:
            params = spec["validate_stored"](client_id, params)
        except ValueError as e:
            _log(f"client {client_id}: prepared {name} failed validation: {e}", severity="WARNING",
                 client_id=client_id)
            return {"error": "invalid_params"}
    else:
        for row in open_rows:
            # Same action + params already open: show that card, don't stack a second
            if (row.get("params") or {}) == params:
                return {"card": card(row, actor, lang), "status": row.get("status")}
    try:
        rows = _db().table(TABLE).insert({
            "client_id": int(client_id), "action": name, "params": params, "origin": origin,
            "persona": persona or "general", "approver": approver, "status": status,
        }).execute().data or []
    except Exception as e:
        _log(f"client {client_id}: proposal {name} not stored: {type(e).__name__}: {e}",
             severity="WARNING", client_id=client_id)
        return {"error": "storage_unavailable"}
    if not rows:
        return {"error": "storage_unavailable"}
    row = rows[0]
    if supersedes:
        # The client's change produced a new card; the old one can no longer be clicked
        _claim(supersedes, OPEN_STATUSES, {"status": "cancelled", "finished_at": _now().isoformat(),
                                           "outcome": "superseded"})
    _log(f"client {client_id}: proposed {name} ({status}) from {origin}/{persona}",
         client_id=client_id, proposal=row.get("id"), action=name, status=status)
    if status == "awaiting_staff":
        _alert(f"client {client_id}: asked in chat for '{name}' — approve or cancel it in the "
               f"Control Room (proposal {row.get('id')})")
    return {"card": card(row, actor, lang), "status": status}


# ─── Decide (the human click) ────────────────────────────────────────────────

def _refuse(code: str, http: int = 400) -> dict:
    return {"success": False, "code": code, "http": http}


def _post(row: dict, text: str, to_client: bool):
    """Outcome into the thread the proposal came from (and, for a staff
    request, also the client's thread — they are waiting for it)."""
    try:
        if row.get("origin") == "client_chat" or to_client:
            _post_client_message(row["client_id"], row.get("persona", "general"), text)
        if row.get("origin") == "admin_chat":
            _post_admin_message(row["client_id"], row.get("persona", "general"), text)
    except Exception as e:
        _log(f"client {row.get('client_id')}: outcome not posted: {type(e).__name__}",
             severity="WARNING", client_id=row.get("client_id"))


def staff_may_decide(spec: dict) -> bool:
    """Whether staff may decide a client-chat card from the admin "enter as
    client" view (core/admin_view): only actions staff can already approve in
    the Control Room (approver admin, or admin_proposable). Every other client
    action — sending e-mail from the client's Gmail, publishing to their
    YouTube or TikTok, ... — stays the client's own click."""
    return spec.get("approver") == "admin" or bool(spec.get("admin_proposable"))


def decide(proposal_id: int, actor: str, actor_client_id: int, decision: str,
           background_tasks=None, staff: bool = False) -> dict:
    """`staff`: a client-chat card decided by staff from the admin view,
    explicitly marked as a staff action — recorded as decided_by 'staff'."""
    if actor not in ("client", "admin"):
        return _refuse("bad_actor", 403)
    if decision not in ("approve", "cancel"):
        return _refuse("bad_decision")
    try:
        row = _get(proposal_id)
    except Exception as e:
        _log(f"proposal {proposal_id}: unreadable ({type(e).__name__})", severity="WARNING")
        return _refuse("storage_unavailable", 503)
    # Another client's proposal is answered exactly like a missing one
    if not row or int(row.get("client_id") or 0) != int(actor_client_id or 0):
        return _refuse("not_found", 404)
    spec = ACTIONS.get(row.get("action"))
    if not spec:
        return _refuse("unknown_action")
    if row.get("approver") != actor:
        return _refuse("not_your_decision", 403)
    if staff and (actor != "client" or not staff_may_decide(spec)):
        return _refuse("client_only", 403)
    by = "staff" if staff else actor
    client_id = int(row["client_id"])
    lang = _lang(client_id)
    if row.get("status") not in OPEN_STATUSES:
        # Double click / second tab: report what already happened, run nothing
        return {"success": True, "already": True, "status": row.get("status"),
                "card": card(row, actor, lang if actor == "client" else "he")}
    stamp = _now().isoformat()
    if _expired(row):
        _claim(proposal_id, OPEN_STATUSES, {"status": "expired", "finished_at": stamp, "outcome": "expired"})
        return {**_refuse("expired", 410), "message": _pick(_EXPIRED, lang)}

    if decision == "cancel":
        if not _claim(proposal_id, OPEN_STATUSES, {"status": "cancelled", "decided_at": stamp,
                                                   "decided_by": by, "finished_at": stamp,
                                                   "outcome": "cancelled"}):
            return {"success": True, "already": True, "status": (_get(proposal_id) or {}).get("status")}
        _log(f"client {client_id}: {row['action']} cancelled by {by}", client_id=client_id,
             proposal=proposal_id)
        _post(row, _pick(_CANCELLED, lang), to_client=row.get("origin") == "client_chat")
        return {"success": True, "status": "cancelled", "message": _pick(_CANCELLED, lang)}

    # Approve can't run right now (outreach while sending is off / Gmail not
    # connected): say why and leave the card exactly as it was — nothing claimed
    blocked = _approve_blocked(spec, client_id)
    if blocked:
        text = _pick((spec.get("refusals") or {}).get(blocked) or _FAILED, lang)
        out = {"success": True, "status": "blocked", "code": blocked, "message": text}
        follow = (spec.get("follow_up") or {}).get(blocked)
        if follow and blocked != "send_disabled":
            fix = propose(client_id, row.get("origin") or "client_chat", row.get("persona", "general"),
                          {"name": follow, "params": {}})
            if fix.get("card"):
                out["next_card"] = fix["card"]
        return out

    # Re-check everything from the STORED row before claiming it
    client = _client(client_id)
    if spec["internal_only"] and not _is_internal(client):
        _claim(proposal_id, OPEN_STATUSES, {"status": "failed", "decided_at": stamp, "decided_by": by,
                                            "finished_at": stamp, "outcome": "refused_internal_only"})
        return _refuse("internal_only", 403)
    try:
        # A prepared action's stored params have their own shape and validator
        params = (spec["validate_stored"](client_id, row.get("params"))
                  if spec.get("validate_stored") else validate_params(spec, row.get("params"), client_id))
    except ValueError as e:
        _log(f"client {client_id}: {row['action']} params refused at approval: {e}",
             severity="WARNING", client_id=client_id, proposal=proposal_id)
        _claim(proposal_id, OPEN_STATUSES, {"status": "failed", "decided_at": stamp, "decided_by": by,
                                            "finished_at": stamp, "outcome": "invalid_params"})
        return _refuse("invalid_params")
    if not _claim(proposal_id, OPEN_STATUSES, {"status": "running", "decided_at": stamp,
                                               "decided_by": by}):
        return {"success": True, "already": True, "status": (_get(proposal_id) or {}).get("status")}
    _log(f"client {client_id}: {row['action']} approved by {by}", client_id=client_id,
         proposal=proposal_id)

    if spec["kind"] in ("link", "focus"):
        _claim(proposal_id, ("running",), {"status": "done", "finished_at": _now().isoformat(),
                                           "outcome": "opened"})
        out = {"success": True, "status": "done", "message": _pick(spec["done"], lang)}
        if spec["kind"] == "link":
            out["redirect"] = spec["link"]
        else:
            # A callable focus names the card from the STORED params
            out["focus"] = spec["focus"](params) if callable(spec["focus"]) else spec["focus"]
        return out

    if spec.get("slow") and background_tasks is not None:
        # Waiting is a card, not a spinner: answer the click NOW with what is
        # running and how long it usually takes; the job runs after the
        # response and the same card polls (status_card) into its result
        background_tasks.add_task(_execute_in_background, row, spec, params, actor, client, lang)
        return {"success": True, "status": "running",
                "message": progress_text(spec, lang if actor == "client" else "he"),
                "progress": {"text": progress_text(spec, lang if actor == "client" else "he"),
                             "started_at": stamp, "stalled": False}}
    return _execute(row, spec, params, actor, client, lang, background_tasks)


def _execute_in_background(row: dict, spec: dict, params: dict, actor: str, client: dict, lang: str):
    """A slow action's run, after the response. Never raises: whatever goes
    wrong outside the action's own handling still ends the row, alerts the
    team and tells the client plainly (never the error itself)."""
    try:
        _execute(row, spec, params, actor, client, lang, None)
    except Exception as e:
        proposal_id = row.get("id")
        _claim(proposal_id, ("running",), {"status": "failed", "finished_at": _now().isoformat(),
                                           "outcome": "error"})
        _log(f"client {row.get('client_id')}: {row.get('action')} background run broke: "
             f"{type(e).__name__}: {e}", severity="WARNING", client_id=row.get("client_id"), proposal=proposal_id)
        _alert(f"client {row.get('client_id')}: approved action '{row.get('action')}' failed in the "
               f"background: {str(e)[:300]}")
        _post(row, _pick(_FAILED, lang), to_client=True)


def _execute(row: dict, spec: dict, params: dict, actor: str, client: dict, lang: str, background_tasks) -> dict:
    """Runs a claimed (status running) action and records + posts its outcome."""
    proposal_id = row.get("id")
    client_id = int(row["client_id"])
    try:
        result = spec["run"](client_id, params, background_tasks) or {}
    except Exception as e:
        code = e.code if isinstance(e, ActionFailed) else "error"
        _claim(proposal_id, ("running",), {"status": "failed", "finished_at": _now().isoformat(),
                                           "outcome": code})
        _log(f"client {client_id}: {row['action']} FAILED ({code}): {type(e).__name__}: {e}",
             severity="WARNING", client_id=client_id, proposal=proposal_id)
        refusal = (spec.get("refusals") or {}).get(code)
        if refusal:
            # An expected, explained refusal (e.g. the daily cap): its own
            # plain line, no alert — nothing is broken
            text = _pick(refusal, lang)
            _post(row, text, to_client=row.get("origin") == "client_chat")
            out = {"success": True, "status": "failed", "code": code, "message": text}
            follow = (spec.get("follow_up") or {}).get(code)
            if follow:
                # The card that fixes it, right under the refusal (e.g. reconnect TikTok)
                fix = propose(client_id, row.get("origin") or "client_chat", row.get("persona", "general"),
                              {"name": follow, "params": {}}, client=client)
                if fix.get("card"):
                    out["next_card"] = fix["card"]
            return out
        if actor == "admin":
            detail = f"✕ {_pick(spec['title'], 'he')} — לא בוצע: {str(e)[:400]}"
            if row.get("origin") == "admin_chat":
                _post_admin_message(client_id, row.get("persona", "general"), detail)
            else:
                # A client's request the team approved: they hear the outcome too
                _post(row, _pick(_FAILED, lang), to_client=True)
            return {"success": True, "status": "failed", "message": detail}
        _alert(f"client {client_id}: approved action '{row['action']}' failed: {str(e)[:300]}")
        _post(row, _pick(_FAILED, lang), to_client=True)
        return {"success": True, "status": "failed", "message": _pick(_FAILED, lang)}

    _claim(proposal_id, ("running",), {"status": "done", "finished_at": _now().isoformat(),
                                       "outcome": "done"})
    _forget_state(client_id)
    done_text = (spec["done_message"] if spec.get("done_message")
                 else (lambda _result, language: _pick(spec["done"], language)))
    message = done_text(result, lang if actor == "client" else "he")
    if row.get("origin") == "client_chat":
        _post(row, done_text(result, lang), to_client=True)
    elif row.get("origin") == "admin_chat":
        _post_admin_message(client_id, row.get("persona", "general"), _admin_summary(spec, result))
    return {"success": True, "status": "done", "message": message,
            "result": result if actor == "admin" else None}


def _admin_summary(spec: dict, result: dict) -> str:
    """The admin's own record of what ran — ids included (admin_agent_chat is
    purged with the client's Google data)."""
    keys = ("campaign_name", "campaign_id", "status", "customer_id", "video_id", "title",
            "privacy_status", "studio_url", "event_name", "live_version", "live_version_id",
            "tag_name", "ga4_measurement_id",
            "site_url", "old_password_revoked")
    facts = " · ".join(f"{k}: {result[k]}" for k in keys if result.get(k))
    return f"✓ {_pick(spec['done'], 'he')}" + (f"\n{facts}" if facts else "")


# ─── Capability check (dry run, admin-triggered) ─────────────────────────────

CHECK_QUESTIONS = (
    ("website", "המדידה באתר שלי עובדת? רואים את הלידים?"),
    ("website", "תעדכן את נתוני המדידה באתר בבקשה"),
    ("google", "ה-Google Ads שלי מחובר?"),
    ("general", "כמה לידים היו לי השבוע?"),
    ("general", "מה רץ אצלי עכשיו?"),
    ("general", "מה מחכה לאישור שלי?"),
    ("google", "כמה הוצאתי החודש על פרסום ביחס לתקציב?"),
    ("website", "האתר שלי מחובר? מה המצב שלו?"),
    ("media", "כמה תמונות וסרטונים יצרתם לי החודש?"),
    ("general", "הכל מחובר אצלי כמו שצריך?"),
)

FORBIDDEN = re.compile("|".join((
    r"לא\s+רוא[הים]", r"אין\s+לי\s+(גישה|אפשרות\s+לראות)", r"לא\s+יכול\s+לראות",
    r"(אעביר|מעביר|העברתי)\s+(את\s+(זה|הבקשה)\s+)?לצוות",
    r"(היכנס|תיכנס|כנס)[וי]?\s+ל", r"(בדק|תבדק|תבדוק)[וי]?\s+בעצמ",
    r"can'?t\s+see", r"don'?t\s+have\s+access", r"pass(ed)?\s+(this|it)\s+(on\s+)?to\s+the\s+team",
    r"log\s*in\s+to", r"check\s+(it\s+)?yourself",
)), re.IGNORECASE)


def capability_check(client_id: int) -> dict:
    """Asks the client's agents CHECK_QUESTIONS in DRY RUN (nothing stored,
    no card, no alert) and flags any reply that dead-ends. A flagged reply on
    a topic whose read genuinely failed this turn is marked as such."""
    from agents import support_agent
    from core import client_state
    state = client_state.snapshot(client_id, refresh=True)
    failed = client_state.failed_topics(state)
    rows = []
    for persona, question in CHECK_QUESTIONS:
        if persona == "general":
            result = support_agent.answer_support_question(client_id, question, dry_run=True)
        else:
            result = support_agent.answer_persona_question(client_id, persona, question, dry_run=True)
        reply = result.get("reply", "")
        hits = sorted({m.group(0) for m in FORBIDDEN.finditer(reply)})
        rows.append({"persona": persona, "question": question, "reply": reply,
                     "proposed_action": result.get("proposed_action") or "",
                     "data_topic": result.get("data_topic", ""),
                     "forbidden_phrases": hits,
                     "topic_read_failed": result.get("data_topic", "") in failed})
    return {"client_id": client_id, "state_failed_sections": client_state.failed_sections(state),
            "flagged": sum(1 for r in rows if r["forbidden_phrases"]), "total": len(rows), "answers": rows}
