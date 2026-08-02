"""uallak's media agent — the creative production hub. Creates VISUAL media
(images / short videos), never text: SEO agent and others write; this agent
makes the visuals they and the human team pull from.

Feeds (pull points for other agents — call these, don't rebuild):
- meta_content_agent: `prepare_for_publishing(client_id, file_id)` returns a
  PUBLIC url a Meta publish spec can use as media_url.
- tiktok_content_agent: no public-url step needed — it takes the Drive
  `file_id` directly and downloads it privately (TikTok's FILE_UPLOAD source
  needs no public link, unlike Meta's media_url; see .claude/skills/tiktok/
  SKILL.md for why PULL_FROM_URL isn't usable here).
- youtube_content_agent: same shape as TikTok — takes the Drive `file_id`
  directly, downloads it privately, uploads raw bytes via YouTube's
  resumable upload (no domain-verification requirement at all, so no
  public-URL step is even possible/needed here).
- seo_agent / website_agent: generated site imagery lands in the client's
  Drive folder (website subfolders); wordpress upload goes through the
  existing wp.upload_media_from_url with the public link.

Storage model — nothing ever gets lost: every asset lands in the client's
Google Drive folder (root: DRIVE_MEDIA_FOLDER_ID, one subfolder per client,
shared read-only with the client's email), organized by type/platform:
  client-{id}/ images/{instagram,facebook,tiktok,website}
               videos/{instagram,facebook,tiktok}
               scripts/           (filming kits / camera coaching docs)
               website-media/{page slugs, when a site is connected}
The client browses it in Drive itself (a dashboard/profile link, no custom UI).

The sacred weekly cadence: every Saturday 20:00 Israel time (מוצאי שבת) the
weekly check-in runs (Cloud Scheduler → /api/media/weekly-checkin) and asks
each media client to confirm next week's content plan — through the existing
client_suggestions approval pipeline (kind='media_plan'), NOT a new channel.
Trend/calendar awareness comes from the engagement engine's context builder
(israel_calendar + performance + business context) — no second trend system.

Camera coaching: `create_filming_kit` closes the sales-chat promise — a real
script + shot list + confidence-building coaching, delivered as a doc in the
client's Drive scripts folder and announced in their dashboard chat.

Reference examples: `send_trend_examples` sends the client real links to
content that is visibly working in THEIR niche, into the media specialist's
own chat thread ("here's how others are doing it well"). The links come from
the same cached competitor_research media lens the generation prompts use —
one research path, not a parallel trend scraper — and are verified to exist
before anyone sees them. Nothing is sent when nothing verified was found.

Human approval is absolute: generated media lands in Drive for review;
NOTHING is auto-published anywhere (same principle as PAUSED campaigns and
draft posts). Bad creative shipped automatically costs real money and trust.

Quality iron rules (baked into every generation prompt):
- NO TEXT inside generated images — every current model renders Hebrew text
  poorly; text overlays are a human/design step on top of clean imagery.
- Brand palette (website_agent's apply_brand_identity record) drives color
  direction when it exists.
- Nothing off-brand, shocking, or trend-chasing for its own sake; the weekly
  plan is where trends enter, deliberately.

TIER 2 extension points (future, DO NOT build here):
- Avatar agent: generation calls accept an optional avatar_context dict that
  v1 ignores — a future avatar agent enriches prompts/models through it.
- AI podcast: a future format on top of generate_video + a voice bank; noted
  direction only.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from supabase import create_client as _supabase_client

from core import competitor_research
from core import drive_service as drive
from core import media_gen_service as gen
from core.agent_base import agent_alert, log_step, timed_step
from core.claude_json import ClaudeJSONError, safe_claude_json_call

AGENT_NAME = "media_agent"

# The client's own Higgsfield Cloud API key (client_accounts row) — THE CLIENT
# pays Higgsfield directly for generation (their plan, their card), we operate
# on their key. Same model as ad spend / SEO tools / WP Application Passwords.
HIGGSFIELD_PLATFORM = "higgsfield"

PLATFORM_FOLDERS = {
    "images": ("instagram", "facebook", "tiktok", "website"),
    "videos": ("instagram", "facebook", "tiktok"),
}
# Platform default aspect ratios (Instagram feed/story habits vs site banners)
IMAGE_ASPECTS = {"instagram": "1:1", "facebook": "1:1", "tiktok": "9:16", "website": "16:9"}
VIDEO_ASPECTS = {"instagram": "9:16", "facebook": "1:1", "tiktok": "9:16"}

MEDIA_PLAN_KIND = "media_plan"
CHECKIN_DEDUP_DAYS = 5  # one Saturday run per week; a rerun must not double-post

_folder_cache = {}  # (client_id) -> {"root": id, "paths": {tuple: id}}

# Created lazily — no DB client at import time (api_server imports every agent at startup)
_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        _db_instance = _supabase_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
    return _db_instance


def _log_activity(client_id: int, action_type: str, details: dict, result: dict = None):
    _db().table("client_activity").insert({
        "client_id": client_id, "agent_name": AGENT_NAME,
        "action_type": action_type, "details": details, "result": result or {},
    }).execute()


# ─── Drive organization ───────────────────────────────────────────────────────

def _media_root_id() -> str:
    from agents.keys_agent import get_key
    return get_key("DRIVE_MEDIA_FOLDER_ID")


def ensure_client_media_root(client_id: int) -> str:
    """Find-or-create client-{id} under the media root; share it (read-only)
    with the client's email the first time. Cached per process."""
    cached = _folder_cache.get(client_id)
    if cached:
        return cached["root"]

    from agents.client_agent import get_client
    client = get_client(client_id)
    folder_name = f"client-{client_id} — {(client.get('name') or 'unnamed').strip()}"
    root = drive.ensure_folder(folder_name, _media_root_id())
    _folder_cache[client_id] = {"root": root, "paths": {}}

    # First-touch side effects, keyed off an activity row so they run once
    already = (_db().table("client_activity").select("id")
               .eq("client_id", client_id).eq("agent_name", AGENT_NAME)
               .eq("action_type", "media_folder_created").limit(1).execute().data)
    if not already:
        if client.get("email"):
            drive.share_with_user(root, client["email"], role="reader")
        _log_activity(client_id, "media_folder_created",
                      {"folder_id": root, "shared_with": client.get("email", "")})
        log_step(AGENT_NAME, "media_root", f"client {client_id}: created + shared {root}")
    return root


def _subfolder(client_id: int, *path: str) -> str:
    """Find-or-create a nested path under the client's media root, e.g.
    _subfolder(cid, 'images', 'instagram'). Cached per process."""
    root = ensure_client_media_root(client_id)
    cache = _folder_cache[client_id]["paths"]
    parent = root
    for depth in range(len(path)):
        key = tuple(path[:depth + 1])
        if key not in cache:
            cache[key] = drive.ensure_folder(path[depth], parent)
        parent = cache[key]
    return parent


def get_client_media_link(client_id: int) -> str:
    """The browsable Drive link for the client's media folder — surfaced in
    the dashboard/profile. Creates the folder on first ask."""
    return drive.get_link(ensure_client_media_root(client_id))


def sync_website_media_folders(client_id: int) -> list:
    """website-media/{page} subfolders mirroring the client's actual site
    pages (dynamic — the handoff's folder spec). No site → no-op."""
    from agents.website_agent import get_site_overview
    overview = get_site_overview(client_id)
    created = []
    if not overview.get("connected"):
        return created
    for page in (overview.get("pages") or []):
        slug = (page.get("title") or "").strip() or f"page-{page.get('id')}"
        _subfolder(client_id, "website-media", slug)
        created.append(slug)
    return created


def prepare_for_publishing(client_id: int, file_id: str) -> dict:
    """Meta publishing needs a PUBLIC media URL (their server fetches it).
    Flips exactly ONE file public and returns the direct link — the client
    folder itself stays restricted. This is meta_content_agent's pull point."""
    url = drive.make_file_public(file_id)
    _log_activity(client_id, "media_prepared_for_publishing", {"file_id": file_id})
    return {"success": True, "media_url": url}


# ─── Generation account (client-paid Higgsfield key) ─────────────────────────

def connect_generation_account(client_id: int, api_key: str) -> dict:
    """Store the client's Higgsfield Cloud API key. Client self-service via
    the dashboard connection card (POST /api/media/connect, session-gated) -
    same pattern as the WordPress Application Password card: the client
    signs up at higgsfield.ai with their own payment method, creates a key
    at cloud.higgsfield.ai/api-keys, and pastes it in themselves. Validated
    by the first real generation, not here (a validation call would burn the
    client's credits)."""
    if not (api_key or "").strip():
        return {"success": False, "errors": ["api_key is required"]}
    from agents.client_agent import upsert_account
    upsert_account(client_id, HIGGSFIELD_PLATFORM, "higgsfield", api_key.strip(), "active")
    _log_activity(client_id, "media_account_connected", {})
    log_step(AGENT_NAME, "connect_generation_account", f"client {client_id}")
    return {"success": True}


def _generation_key(client_id: int) -> str:
    rows = (_db().table("client_accounts").select("access_token")
            .eq("client_id", client_id).eq("platform", HIGGSFIELD_PLATFORM)
            .eq("status", "active").order("id", desc=True).limit(1).execute().data)
    return (rows[0].get("access_token") or "") if rows else ""


_NO_ACCOUNT_ERROR = ("client's Higgsfield account is not connected - the client connects it "
                     "themselves from the dashboard's connection card (POST /api/media/connect, "
                     "same self-service pattern as WordPress; see the media skill)")


def get_monthly_usage(client_id: int) -> dict:
    """Generation activity this calendar month — the pull point for
    budget_agent (and anything else wanting a Higgsfield usage summary
    without re-querying client_activity itself). Credits are the CLIENT'S
    own Higgsfield plan credits, not an ILS/USD figure - Higgsfield exposes
    no spend/billing API, so a currency amount here would be a fabricated
    number. `credits_total` is None (not 0) when every generation this month
    came back without a credits field at all, so callers can tell "zero
    usage" apart from "usage happened but the field was empty"."""
    month_start = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    rows = (_db().table("client_activity").select("action_type,details,created_at")
            .eq("client_id", client_id).eq("agent_name", AGENT_NAME)
            .in_("action_type", ["media_image_created", "media_video_created"])
            .gte("created_at", month_start).limit(1000).execute().data or [])
    images = [r for r in rows if r["action_type"] == "media_image_created"]
    videos = [r for r in rows if r["action_type"] == "media_video_created"]
    credit_values = [(r.get("details") or {}).get("credits") for r in rows]
    known_credits = [c for c in credit_values if isinstance(c, (int, float))]
    return {
        "connected": bool(_generation_key(client_id)),
        "images_this_month": len(images),
        "videos_this_month": len(videos),
        "credits_total": round(sum(known_credits), 2) if known_credits else None,
        "credits_unit_known": bool(known_credits) and len(known_credits) == len(rows),
    }


# ─── Generation (images / videos) ─────────────────────────────────────────────

PROMPT_SYSTEM = """You are the creative director of uallak, an Israeli marketing agency,
turning ONE content brief for a small business into ONE excellent English generation prompt
for an image/video model.

IRON RULES:
- The prompt must describe imagery with ABSOLUTELY NO text, letters, words, signs with
  writing, logos, or captions in the scene (text is overlaid later by designers — every
  current model renders Hebrew badly). Explicitly steer away from text.
- Grounded in THIS business (its industry, audience, setting — from the context given);
  authentic Israeli small-business feel, not generic stock-photo gloss.
- If a brand palette (hex colors) is provided, weave those tones into the scene naturally.
- When competitor_research is provided, use it as DIRECTION, never as a template: match the
  register that visibly works in this niche (shot type, setting, polish level) and prefer
  the stated GAP over what everyone already does. NEVER reproduce a specific competitor's
  scene, composition or concept — if the research names a competitor's asset, deliberately
  make something different that works for the same audience. Research absent or empty is
  normal: fall back to the business context alone, never to generic stock imagery.
- Professional, warm, high-quality photography/cinematography language. No shock value,
  no celebrity likenesses, no trademarked characters or brands.
- max 120 words in generation_prompt. One clear scene, not a collage of ideas.

Return JSON only:
{"generation_prompt": "English prompt", "rationale": "1 English sentence"}"""


def _brand_palette(client_id: int) -> list:
    rows = (_db().table("client_activity").select("details")
            .eq("client_id", client_id).eq("action_type", "website_brand_identity")
            .order("created_at", desc=True).limit(1).execute().data)
    return ((rows[0].get("details") or {}).get("palette") or []) if rows else []


def _business_brief(client_id: int) -> dict:
    from agents.seo_agent import _business_context
    context = _business_context(client_id)
    context.pop("sales_chat_answers", None)  # prompt-crafting needs the essence, not the transcript
    return context


def _craft_prompt(client_id: int, brief: str, platform: str, kind: str,
                  avatar_context: dict = None) -> dict:
    payload = {
        "business": _business_brief(client_id),
        "brief": brief,
        "platform": platform,
        "media_kind": kind,
        "brand_palette": _brand_palette(client_id),
    }
    # Look at the niche before generating for it. Cached ~2 weeks per client in
    # core/competitor_research, so a client generating ten assets in a day pays
    # for one research pass, not ten — and an empty string (research failed or
    # unavailable) simply leaves the prompt grounded in business context alone,
    # exactly as it was before this step existed.
    research = competitor_research.summary_for_prompt(client_id, "media")
    if research:
        payload["competitor_research"] = research
    # TIER-2 extension point: a future avatar agent enriches prompts here
    if avatar_context:
        payload["avatar_context"] = avatar_context
    return safe_claude_json_call(PROMPT_SYSTEM, json.dumps(payload, ensure_ascii=False),
                                 max_tokens=500, client_id=client_id,
                                 cost_category="claude_media")


def generate_image(client_id: int, brief: str, platform: str = "instagram",
                   avatar_context: dict = None) -> dict:
    """Brief (any language) → crafted prompt → Higgsfield image model → the
    client's Drive folder for human review. Runs on the CLIENT'S key and
    credits. Never publishes anything."""
    platform = platform if platform in PLATFORM_FOLDERS["images"] else "instagram"
    api_key = _generation_key(client_id)
    if not api_key:
        return {"success": False, "errors": [_NO_ACCOUNT_ERROR]}
    log_step(AGENT_NAME, "generate_image", f"client {client_id} [{platform}]: {brief[:80]}")
    try:
        crafted = timed_step(AGENT_NAME, "craft_prompt",
                             lambda: _craft_prompt(client_id, brief, platform, "image",
                                                   avatar_context))
        generated = timed_step(
            AGENT_NAME, "higgsfield_image",
            lambda: gen.generate_image(api_key, crafted["generation_prompt"],
                                       IMAGE_ASPECTS[platform]))
        folder = _subfolder(client_id, "images", platform)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
        uploaded = drive.upload_bytes(folder, f"{platform}-{stamp}.png",
                                      generated["content"], generated["mime"])
    except Exception as e:  # includes ClaudeJSONError
        agent_alert(AGENT_NAME, [f"client {client_id}: image generation failed ({brief[:60]}): {e}"])
        return {"success": False, "errors": [str(e)]}

    # credits = the CLIENT'S spend on their own Higgsfield plan (never ours -
    # deliberately not written to client_costs)
    _log_activity(client_id, "media_image_created",
                  {"platform": platform, "brief": brief[:200],
                   "prompt": crafted["generation_prompt"],
                   "model": generated.get("model"), "credits": generated.get("credits")},
                  {"file_id": uploaded.get("id"), "link": uploaded.get("webViewLink", "")})
    return {"success": True, "file_id": uploaded.get("id"),
            "link": uploaded.get("webViewLink", ""), "platform": platform,
            "prompt": crafted["generation_prompt"]}


def generate_video(client_id: int, brief: str, platform: str = "instagram",
                   avatar_context: dict = None) -> dict:
    """Brief → crafted prompt → Higgsfield video model (Veo-class, short clip
    with native audio) → Drive. Runs on the CLIENT'S key and credits —
    daily-capped in the service to protect their balance, and always
    human-reviewed in Drive."""
    platform = platform if platform in PLATFORM_FOLDERS["videos"] else "instagram"
    api_key = _generation_key(client_id)
    if not api_key:
        return {"success": False, "errors": [_NO_ACCOUNT_ERROR]}
    log_step(AGENT_NAME, "generate_video", f"client {client_id} [{platform}]: {brief[:80]}")
    try:
        crafted = timed_step(AGENT_NAME, "craft_prompt",
                             lambda: _craft_prompt(client_id, brief, platform, "video",
                                                   avatar_context))
        generated = timed_step(
            AGENT_NAME, "higgsfield_video",
            lambda: gen.generate_video(api_key, crafted["generation_prompt"],
                                       VIDEO_ASPECTS[platform]))
        folder = _subfolder(client_id, "videos", platform)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
        uploaded = drive.upload_bytes(folder, f"{platform}-{stamp}.mp4",
                                      generated["content"], generated["mime"])
    except Exception as e:  # includes ClaudeJSONError
        agent_alert(AGENT_NAME, [f"client {client_id}: video generation failed ({brief[:60]}): {e}"])
        return {"success": False, "errors": [str(e)]}

    _log_activity(client_id, "media_video_created",
                  {"platform": platform, "brief": brief[:200],
                   "prompt": crafted["generation_prompt"],
                   "model": generated.get("model"), "credits": generated.get("credits")},
                  {"file_id": uploaded.get("id"), "link": uploaded.get("webViewLink", "")})
    return {"success": True, "file_id": uploaded.get("id"),
            "link": uploaded.get("webViewLink", ""), "platform": platform,
            "prompt": crafted["generation_prompt"]}


# ─── Camera coaching (delivers the sales-chat promise) ────────────────────────

FILMING_KIT_SYSTEM = """You are uallak's filming coach for Israeli small-business owners —
warm, practical, confidence-building. The owner will film THEMSELVES on a phone. Produce a
complete filming kit for ONE short video on the given topic, in the CLIENT'S LANGUAGE
(the language of their business context/brief; Hebrew default).

When competitor_research is provided it is OBSERVED evidence about what visibly works in
this exact niche — let it shape the hook, the pacing and the shot list, and prefer its
stated GAP. Use it as DIRECTION, never as a template: never rebuild a specific competitor's
video, and never mention a competitor by name in the script the owner will speak. Research
absent or empty is normal — write the kit from the business context alone.

Structure (hard limits — this must fit on ~1 page):
- script: the exact words to say, 60-90 seconds of natural speech, written the way THIS
  owner would actually talk (from the business context), with a strong first-3-seconds hook.
- shot_list: 3-5 shots, each 1 line (what to film, where to stand, phone orientation).
- coaching: 4-6 short, warm tips that build confidence (light, eye-line, energy, retakes are
  normal, imperfect is authentic). Encourage, never lecture.
- gear_note: 1-2 sentences — phone is enough; what cheap helpers matter (daylight, a stand).

Return JSON only:
{"title": "client-language title", "script": "client-language text",
 "shot_list": ["..."], "coaching": ["..."], "gear_note": "..."}"""


def create_filming_kit(client_id: int, topic: str) -> dict:
    """Script + shot list + camera coaching for self-filming — saved as a doc
    in the client's Drive scripts folder and announced in their dashboard
    chat. This is the deliverable the sales chat promises."""
    topic = (topic or "").strip()
    if not topic:
        return {"success": False, "errors": ["topic is required"]}
    log_step(AGENT_NAME, "filming_kit", f"client {client_id}: {topic[:80]}")
    payload = {"business": _business_brief(client_id), "topic": topic}
    # The script is content too, so it gets the same niche evidence the image
    # and video prompts already get — same cached lens, no extra research pass.
    research = competitor_research.summary_for_prompt(client_id, "media")
    if research:
        payload["competitor_research"] = research
    try:
        kit = timed_step(
            AGENT_NAME, "filming_kit_llm",
            lambda: safe_claude_json_call(FILMING_KIT_SYSTEM,
                                          json.dumps(payload, ensure_ascii=False),
                                          max_tokens=1500, client_id=client_id,
                                          cost_category="claude_media"))
        doc = "\n\n".join([
            kit.get("title", topic),
            "🎬 " + (kit.get("script") or ""),
            "📋 Shot list:\n" + "\n".join(f"• {s}" for s in kit.get("shot_list") or []),
            "💪 Coaching:\n" + "\n".join(f"• {c}" for c in kit.get("coaching") or []),
            "🎒 " + (kit.get("gear_note") or ""),
        ])
        folder = _subfolder(client_id, "scripts")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        uploaded = drive.upload_bytes(folder, f"filming-kit-{stamp}.txt",
                                      doc.encode("utf-8"), "text/plain")
    except Exception as e:  # includes ClaudeJSONError
        agent_alert(AGENT_NAME, [f"client {client_id}: filming kit failed ({topic[:60]}): {e}"])
        return {"success": False, "errors": [str(e)]}

    from agents.client_agent import log_communication
    _log_activity(client_id, "media_filming_kit_created", {"topic": topic[:200]},
                  {"file_id": uploaded.get("id"), "link": uploaded.get("webViewLink", "")})
    log_communication(client_id, "outbound", "dashboard_chat",
                      f'הכנו לך ערכת צילום מלאה ("{kit.get("title", topic)}") — תסריט, רשימת שוטים '
                      f'וטיפים לצילום עצמי בטלפון 🎬 מחכה לך בתיקיית ה-Drive שלך: '
                      f'{uploaded.get("webViewLink", "")}')
    return {"success": True, "file_id": uploaded.get("id"),
            "link": uploaded.get("webViewLink", ""), "kit": kit}


# ─── Reference examples: "here's how others in your niche are doing it" ───────
#
# The client asked to see what actually works in their space, not to be told
# about it. This sends REAL links to real content from their own niche into
# their chat, in the media specialist's voice.
#
# Three deliberate constraints:
# 1. NO parallel research path. The links come out of the ONE media lens in
#    core/competitor_research that every generation already pays for, via
#    `media_examples()` — same cache, same fortnightly cost, no second prompt to
#    drift. That is also why this lives in media_agent and not in a new agent.
# 2. Links are never written by the LLM. The model writes only the Hebrew prose;
#    the URLs are pasted in by CODE from the verified example rows, so a model
#    that mangles or shortens a URL cannot produce a dead link.
# 3. Nothing is sent when there is nothing verified to send. No examples, no
#    message — never a padded "look at your competitors!" nudge with no links,
#    which is the whole value of the message.

TREND_EXAMPLES_DEDUP_DAYS = 30  # never send a client the same link twice in a month

TREND_EXAMPLES_SYSTEM = """You are {name}, uallak's visual-content specialist, writing ONE
short proactive message to an existing paying client in their private dashboard chat.

YOUR VOICE: {voice} You are a member of the team wrapped around this client — never say you
are an AI or a bot.

You are given: this client's business context, and EXAMPLES of real content from other
businesses in THEIR niche that is visibly working (each with the creator's name and a note
on why it works). The links themselves are added afterwards by the system — do NOT write,
repeat, or invent any URL.

Write, in HEBREW (this is a Hebrew-facing dashboard):
- intro: 1-2 sentences. Why you are sending this: you looked at what is working in their
  space and picked a few worth watching. Warm, concrete, no hype.
- notes: ONE note per example, in the SAME ORDER as the examples given, max 12 words each.
  What to actually notice in it (the hook, the format, the angle) — not a summary of it.
- closing: 1 sentence inviting them to tell you which direction appeals, so you can build
  the next script around it.

IRON RULES: never claim a view count, follower count or "trending" status — you cannot see
those. Never suggest copying anyone; the point is direction and inspiration. Never promise
results. No markdown, no bullet characters, no URLs.

Return JSON only:
{{"intro": "Hebrew text", "notes": ["Hebrew", "..."], "closing": "Hebrew text"}}"""


def _recently_sent_example_urls(client_id: int) -> set:
    """Links already sent to this client lately. The research cache (14 days)
    outlives a weekly cadence, so without this the Saturday run would send the
    same three links a fortnight running."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=TREND_EXAMPLES_DEDUP_DAYS)).isoformat()
    rows = (_db().table("client_activity").select("details")
            .eq("client_id", client_id).eq("agent_name", AGENT_NAME)
            .eq("action_type", "media_trend_examples_sent")
            .gte("created_at", cutoff).limit(20).execute().data or [])
    return {url for row in rows for url in ((row.get("details") or {}).get("urls") or [])}


def send_trend_examples(client_id: int) -> dict:
    """Send the client links to content that is working in their niche, through
    the media specialist's own chat thread.

    Returns {"success", "sent": bool, "examples": [...]}. `sent: False` with no
    error is the normal, honest outcome when research found no link it could
    stand behind (see core/competitor_research's verification) or when every
    link found was already sent recently — this NEVER pads a message to have
    something to say."""
    log_step(AGENT_NAME, "trend_examples", f"client {client_id}: gathering")
    try:
        examples = competitor_research.media_examples(client_id)
    except Exception as e:  # research is enrichment; it must never raise upward
        log_step(AGENT_NAME, "trend_examples", f"client {client_id}: research unavailable: {e}")
        return {"success": True, "sent": False, "reason": "research_unavailable", "examples": []}

    already_sent = _recently_sent_example_urls(client_id)
    examples = [e for e in examples if e.get("url") and e["url"] not in already_sent]
    if not examples:
        log_step(AGENT_NAME, "trend_examples", f"client {client_id}: nothing new to send")
        return {"success": True, "sent": False, "reason": "no_new_examples", "examples": []}

    from agents.support_agent import PERSONAS, persona_channel
    persona = PERSONAS["media"]
    payload = {
        "business": _business_brief(client_id),
        "examples": [{"creator": e["creator"], "why": e["why"]} for e in examples],
    }
    try:
        written = timed_step(
            AGENT_NAME, "trend_examples_llm",
            lambda: safe_claude_json_call(
                TREND_EXAMPLES_SYSTEM.format(name=persona["name"], voice=persona["voice"]),
                json.dumps(payload, ensure_ascii=False),
                max_tokens=700, client_id=client_id, cost_category="claude_media"))
    except Exception as e:  # includes ClaudeJSONError
        agent_alert(AGENT_NAME, [f"client {client_id}: trend examples message failed: {e}"])
        return {"success": False, "sent": False, "errors": [str(e)]}

    # The URLs are OURS, not the model's — assembled here from the verified rows.
    # Every model-written field is coerced: a note that comes back as a number or
    # a nested object must not crash a message that is only ever a bonus.
    notes = written.get("notes") or []
    lines = [str(written.get("intro") or "").strip()]
    for index, example in enumerate(examples):
        note = str(notes[index] or "").strip() if index < len(notes) else ""
        creator = example.get("creator") or ""
        headline = " — ".join(part for part in (creator, note or example.get("why", "")) if part)
        lines.append(f"\n{headline}\n{example['url']}" if headline else f"\n{example['url']}")
    closing = str(written.get("closing") or "").strip()
    if closing:
        lines.append(f"\n{closing}")
    message = "\n".join(line for line in lines if line).strip()

    # The MEDIA specialist's own thread, not the general concierge: this is
    # ליאור's subject, and personas keep separate conversations by channel.
    from agents.client_agent import log_communication
    log_communication(client_id, "outbound", persona_channel("media"), message)
    _log_activity(client_id, "media_trend_examples_sent",
                  {"count": len(examples), "urls": [e["url"] for e in examples],
                   "creators": [e.get("creator", "") for e in examples]},
                  {"channel": persona_channel("media")})
    log_step(AGENT_NAME, "trend_examples", f"client {client_id}: sent {len(examples)} example(s)")
    return {"success": True, "sent": True, "examples": examples, "message": message}


# ─── The sacred Saturday-night weekly check-in ────────────────────────────────

WEEKLY_PLAN_SYSTEM = """You are uallak's creative planner, proposing NEXT WEEK'S visual
content plan for ONE Israeli small business, to be confirmed by the client with one tap.
You receive their business context, connected platforms, campaign performance when
available, upcoming Israeli calendar events, and recently made suggestions.

Propose 2-3 concrete MEDIA items for the coming week. Each must be:
- Visual (an image, a short video, or a self-filmed clip we'd coach them through) — never
  a text-only idea.
- Tied to something real: an upcoming calendar event, their stated goals, a proven
  format for their industry (your confident knowledge — NEVER invented "viral this week"
  claims; trends enter deliberately, not franticly), or the competitor_research when it is
  provided — which is observed evidence about this niche, so prefer it over generic
  instinct. Use it for DIRECTION and prefer its stated GAP; never propose reproducing a
  competitor's specific piece of content.
- Something uallak actually produces after approval (generated image/video, or a filming
  kit for them to film).
Respect sensitive calendar events: near one, propose toned-down content, not promotions.

Rules: don't repeat recent titles. Hebrew only (this surfaces in the Hebrew dashboard).
HARD LIMITS: max 3 items; title max 10 words; body 2-3 sentences ending with what we'll
prepare once they approve.

Return JSON only:
{"items": [{"title": "Hebrew", "body": "Hebrew", "format": "image|video|self_filmed",
            "platform": "instagram|facebook|tiktok|website", "event_slug": ""}]}"""


def _recent_media_plan_exists(client_id: int) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=CHECKIN_DEDUP_DAYS)).isoformat()
    rows = (_db().table("client_suggestions").select("id")
            .eq("client_id", client_id).eq("kind", MEDIA_PLAN_KIND)
            .gte("created_at", cutoff).limit(1).execute().data)
    return bool(rows)


def _checkin_for_client(client: dict, events: list) -> int:
    """One client's Saturday check-in: plan → client_suggestions rows
    (kind=media_plan) → chat nudge. Returns items stored."""
    client_id = client["id"]
    if _recent_media_plan_exists(client_id):
        return 0
    # Reuse the engagement engine's context builder — calendar + performance +
    # business context, one source of truth (no second trend mechanism)
    from agents.engagement_agent import _client_context
    payload = _client_context(client, events)
    # The weekly plan is where most media briefs are actually born, so it gets
    # the same niche evidence the per-asset prompt does. Cached, and silently
    # skipped when unavailable — the sacred Saturday run must never fail on it.
    research = competitor_research.summary_for_prompt(client_id, "media")
    if research:
        payload["competitor_research"] = research
    result = safe_claude_json_call(WEEKLY_PLAN_SYSTEM, json.dumps(payload, ensure_ascii=False),
                                   max_tokens=900, client_id=client_id,
                                   cost_category="claude_media")
    stored = []
    for item in (result.get("items") or [])[:3]:
        title, body = (item.get("title") or "").strip(), (item.get("body") or "").strip()
        if not title or not body:
            continue
        _db().table("client_suggestions").insert({
            "client_id": client_id, "kind": MEDIA_PLAN_KIND,
            "title": title, "body": body, "source": "media_weekly",
            "context": {"format": item.get("format", ""), "platform": item.get("platform", ""),
                        "event_slug": item.get("event_slug", "")},
            "status": "pending",
        }).execute()
        stored.append(title)

    if stored:
        from agents.client_agent import log_activity, log_communication
        log_activity(client_id, AGENT_NAME, "media_plan_proposed",
                     {"count": len(stored), "titles": stored}, {})
        bullets = "\n".join(f"• {t}" for t in stored)
        log_communication(client_id, "outbound", "dashboard_chat",
                          f"שבוע טוב! 🌙 הנה תוכנית התוכן הוויזואלי שהכנו לשבוע הקרוב:\n{bullets}\n"
                          'אשרו מה שמתאים באזור "ממתין לאישור שלך" — ומיד נתחיל להכין. '
                          "רוצים משהו אחר? כתבו לי כאן.")
        # Same beat, the other half of the promise: the plan says what we'll
        # make, the examples show what is already working in their niche. The
        # research it reads is the one this check-in just used, so it is a
        # cache hit, not a second paid pass. Fully swallowed: an example
        # message is a bonus, and the sacred run must never fail because of it.
        try:
            send_trend_examples(client_id)
        except Exception as e:
            log_step(AGENT_NAME, "trend_examples",
                     f"client {client_id}: skipped during weekly check-in: {e}")
    return len(stored)


def run_weekly_media_checkin() -> dict:
    """THE sacred cadence — Cloud Scheduler, Saturdays 20:00 Asia/Jerusalem
    (מוצאי שבת). Runs for clients assigned to this agent (client_agents rows,
    admin assigns via POST /api/clients/{id}/agents). Dedup guard makes an
    accidental rerun harmless."""
    from agents.client_agent import get_client
    from core import israel_calendar
    rows = (_db().table("client_agents").select("client_id")
            .eq("agent_name", AGENT_NAME).eq("status", "active").execute().data or [])
    events = israel_calendar.upcoming_events()
    log_step(AGENT_NAME, "weekly_checkin", f"{len(rows)} media clients")
    summary = {"clients": len(rows), "plans_proposed": 0, "skipped": 0, "failures": 0}
    for row in rows:
        try:
            client = get_client(row["client_id"])
            if not client or client.get("status") != "active":
                summary["skipped"] += 1
                continue
            count = timed_step(AGENT_NAME, f"client_{row['client_id']}",
                               lambda c=client: _checkin_for_client(c, events))
            if count:
                summary["plans_proposed"] += 1
            else:
                summary["skipped"] += 1
        except Exception as e:  # one client never kills the sacred run
            summary["failures"] += 1
            agent_alert(AGENT_NAME, [f"weekly media check-in failed for client {row['client_id']}: {e}"])
    log_step(AGENT_NAME, "weekly_checkin", f"done — {summary}")
    return summary
