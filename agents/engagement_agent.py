"""uallak's proactive engagement engine — the shift from "answer when asked"
to "drive the relationship". Four jobs:

1. WEEKLY suggestions (run_weekly_engagement, Cloud Scheduler → /api/engagement/weekly):
   one LLM call per active client that combines three proactive angles —
   Israeli calendar preparation (core/israel_calendar.py, suggestions appear
   weeks BEFORE the chag, and "sensitive" dates advise toning ads down),
   trend/industry ideas from Claude's own confident knowledge (same
   knowledge-not-paid-tools reasoning as the sales chat's market_reality —
   ranges and professional judgment, never fabricated "viral right now"
   claims), and performance-grounded tweaks when the client has connected
   ad accounts. Results land as PENDING rows in client_suggestions — the
   dashboard's "ממתין לאישור שלך" area — plus a short push into the
   dashboard chat. Nothing executes without client approval.

2. DAILY sales alerts (run_daily_engagement → /api/engagement/daily):
   yesterday's conversions per connected platform → a celebration email
   (distinct from weekly reports). Deduped per-day via client_activity.

3. URGENT notifications (notify_client_urgent): the WhatsApp SOS rung of the
   notification ladder (dashboard = ambient, email = important, WhatsApp =
   can't wait), used by the ads health scans when a campaign gets
   auto-paused. Falls back to a dashboard-chat message when WhatsApp is
   unconfigured/failed, and alerts the team on real send failures.

4. LOGIN MOMENTS (run_login_moment, called when the client opens the
   dashboard): the only REACTIVE job here — the other three are cron. Code
   evaluates the triggers (time of day, pending items on the client's side,
   what we delivered since last week, new leads, the weekly satisfaction ask)
   and only if something is genuinely worth saying does ONE LLM call write
   ONE message about it. Deduped to once per client per day. Deliberately NOT
   templated: this is a daily surface, and the f-string pushes elsewhere in
   this file would read as dead within a week here. Also owns the platform
   FEEDBACK store (store_feedback/list_feedback) — the answer to the weekly
   ask, and the one thing in this file addressed to us rather than to the
   client's market.

Suggestion lifecycle: pending → approved/rejected by the client in the
dashboard (see /api/client/suggestions endpoints). An approval alerts the
team — v1 fulfillment is human; agents pick approved work up as their
execution surfaces grow. Future client-facing AI avatars will reuse this
same suggestion/approval pipe — don't fold approval UX into chat replies.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from supabase import create_client as _supabase_client

from core import israel_calendar
from core.agent_base import agent_alert, log_step, timed_step
from core.claude_json import safe_claude_json_call
from core.whatsapp_service import is_configured as whatsapp_configured, send_whatsapp

AGENT_NAME = "engagement_agent"

MAX_SUGGESTIONS_PER_RUN = 3
RECENT_TITLES_LIMIT = 10          # passed to the prompt to avoid repeats
VALID_SUGGESTION_KINDS = ("promotion", "content_idea", "campaign_tweak", "homework")
PLATFORM_LABELS_HE = {"google_ads": "גוגל", "meta_ads": "פייסבוק ואינסטגרם"}

WEEKLY_SYSTEM = """You are the proactive marketing brain of uallak, an Israeli marketing agency,
generating this week's suggestions for ONE existing client. You receive their business context
(intro/answers, proposal summary, connected platforms), compact recent campaign metrics when
available, upcoming Israeli calendar events (each with days_until, kind, and a marketing angle),
and titles of suggestions already made recently.

Generate 2-3 NEW suggestions the client can approve or reject with one tap:
- CALENDAR: if an upcoming event is relevant to THIS business, one concrete preparation
  suggestion for it (specific promotion/content concept + why now). Events with kind
  "sensitive" are NOT promo opportunities — if one is imminent, suggest toning down or
  pausing scheduled promotional content around it instead.
- TREND: one idea from your own confident knowledge of this industry in Israel (formats,
  consumer behavior, platform habits). Professional judgment with approximations — NEVER
  fabricated statistics, and NEVER claims about what is viral "this week" (you cannot know).
- PERFORMANCE: only if metrics are present, one concrete tweak grounded strictly in the
  numbers given — cite them. Skip entirely if no metrics.

Rules: never repeat or trivially rephrase the recent titles. Every suggestion must be
something uallak can actually do for them (content, campaigns, site, promotions) — when
materials are needed, say the team will prepare drafts after approval. Hebrew only.
HARD LIMITS: max 3 suggestions; title max 10 words; body 2-3 sentences.

Return JSON only:
{"suggestions": [{"kind": "promotion|content_idea|campaign_tweak|homework",
                  "title": "Hebrew", "body": "Hebrew",
                  "source": "holiday|trend|performance|general", "event_slug": ""}]}"""

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


# ─── Suggestions store ────────────────────────────────────────────────────────

def get_suggestions(client_id: int, status: str = "pending", limit: int = 20) -> list:
    query = (_db().table("client_suggestions").select("*")
             .eq("client_id", client_id).order("created_at", desc=True).limit(limit))
    if status:
        query = query.eq("status", status)
    return query.execute().data or []


def decide_suggestion(client_id: int, suggestion_id: int, decision: str, background_tasks=None) -> dict:
    """Client taps approve/reject in the dashboard. Ownership-checked (the
    row must belong to the session's client) and only pending rows move.

    On approval, dispatches kind-specific AUTOMATIC fulfillment when a clean
    one exists (currently: media_plan -> media_agent generation/filming-kit,
    see _AUTO_FULFILL) via `background_tasks` (a starlette BackgroundTasks
    instance, duck-typed here so this module doesn't import fastapi —
    generation can take up to ~10 minutes and must never block the client's
    approve-tap request). Kinds with no clean 1:1 automatic mapping
    (promotion/content_idea/campaign_tweak/homework) still just alert the
    team for human fulfillment, same as before — a deliberate, flagged
    choice (see the engagement skill), not an oversight: those are
    open-ended ideas needing creative/business judgment, not a single
    deterministic function call."""
    if decision not in ("approved", "rejected"):
        return {"success": False, "error": "decision must be approved|rejected"}
    rows = (_db().table("client_suggestions").select("*")
            .eq("id", suggestion_id).eq("client_id", client_id)
            .eq("status", "pending").limit(1).execute().data)
    if not rows:
        return {"success": False, "error": "suggestion not found or already decided"}
    suggestion = rows[0]
    _db().table("client_suggestions").update(
        {"status": decision, "decided_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", suggestion_id).execute()

    from agents.client_agent import log_activity
    log_activity(client_id, AGENT_NAME, f"suggestion_{decision}",
                 {"suggestion_id": suggestion_id, "title": suggestion.get("title", "")}, {})
    if decision == "approved":
        outcome = _dispatch_approved(client_id, suggestion, background_tasks)
        agent_alert(AGENT_NAME, [f"client {client_id} APPROVED suggestion "
                                 f"'{suggestion.get('title', '')}' (#{suggestion_id}) — {outcome}"])
    return {"success": True, "id": suggestion_id, "status": decision}


# ─── Kind-specific automatic fulfillment (closes the approve→execute gap) ─────

def _fulfill_media_plan(client_id: int, suggestion: dict):
    """Runs in the BACKGROUND after a media_plan suggestion is approved —
    never inline in the client's approve-tap request (generation can take
    up to ~10 minutes; media_gen_service's polling). generate_image/
    generate_video/create_filming_kit already alert on their own expected
    failure paths — this only needs a safety net for anything that escapes
    them, since an exception in a background task has no other visibility
    to a human."""
    from agents.media_agent import create_filming_kit, generate_image, generate_video
    context = suggestion.get("context") or {}
    fmt = context.get("format", "image")
    platform = context.get("platform", "instagram")
    brief = f"{suggestion.get('title', '')} — {suggestion.get('body', '')}".strip(" —")
    try:
        if fmt == "self_filmed":
            create_filming_kit(client_id, suggestion.get("title", ""))
        elif fmt == "video":
            generate_video(client_id, brief, platform=platform)
        else:
            generate_image(client_id, brief, platform=platform)
    except Exception as e:
        agent_alert(AGENT_NAME, [f"client {client_id}: automatic fulfillment of approved media "
                                 f"suggestion '{suggestion.get('title', '')}' crashed unexpectedly "
                                 f"({e}) — needs manual follow-up"])


# kind -> background handler. Only kinds with a clean, safe-to-run-unattended
# mapping onto an existing agent function belong here — see the docstring
# above and the engagement skill for why the other kinds are excluded.
_AUTO_FULFILL = {
    "media_plan": _fulfill_media_plan,
}


def _dispatch_approved(client_id: int, suggestion: dict, background_tasks) -> str:
    handler = _AUTO_FULFILL.get(suggestion.get("kind"))
    if not handler:
        return "action needed"  # unchanged v1 behavior for kinds with no automatic mapping
    if background_tasks is None:
        # Called from a context that can't run this safely in the background
        # (e.g. a future non-HTTP caller) - never block synchronously on a
        # ~10-minute generation call; leave it for manual follow-up instead.
        return "action needed (automatic fulfillment unavailable in this context)"
    background_tasks.add_task(handler, client_id, suggestion)
    return "approved — generation/preparation starting automatically in the background"


# ─── Weekly engagement run ────────────────────────────────────────────────────

def _client_context(client: dict, events: list) -> dict:
    """Everything the weekly prompt needs about one client, kept compact —
    response length is latency, but a bloated INPUT is cost for zero gain."""
    client_id = client["id"]
    lead = {}
    if client.get("email"):
        rows = (_db().table("leads").select("answers,proposal")
                .eq("client_email", client["email"])
                .order("created_at", desc=True).limit(1).execute().data)
        lead = rows[0] if rows else {}
    answers = lead.get("answers") or {}
    proposal = lead.get("proposal") or {}

    connections = (_db().table("client_accounts").select("platform,status")
                   .eq("client_id", client_id).eq("status", "active").execute().data or [])
    platforms = sorted({c["platform"] for c in connections})

    performance = {}
    if "google_ads" in platforms:
        from agents.google_ads_agent import get_campaign_performance
        perf = get_campaign_performance(client_id)
        if perf.get("connected") and not perf.get("error"):
            performance["google_ads_last30d"] = perf.get("totals", {})
    if "meta_ads" in platforms:
        from agents.meta_ads_agent import get_campaign_performance
        perf = get_campaign_performance(client_id)
        if perf.get("connected") and not perf.get("error"):
            performance["meta_ads_last30d"] = perf.get("totals", {})

    recent_titles = [s.get("title", "") for s in
                     get_suggestions(client_id, status="", limit=RECENT_TITLES_LIMIT)]

    return {
        "business": {
            "name": client.get("name", ""),
            "package": client.get("package", ""),
            "intro": (answers.get("intro") or "")[:600],
            "main_goal": answers.get("main_goal", ""),
            "business_summary": proposal.get("business_summary", ""),
            "recommended_services":
                ((proposal.get("packages") or [{}])[0]).get("recommended_services", []),
        },
        "connected_platforms": platforms,
        "campaign_performance": performance,
        "upcoming_israel_events": events,
        "recent_suggestion_titles": recent_titles,
    }


def _generate_for_client(client: dict, events: list) -> int:
    """One client's weekly suggestions: LLM call → validated pending rows →
    chat push. Returns how many suggestions were stored."""
    client_id = client["id"]
    payload = _client_context(client, events)
    result = safe_claude_json_call(
        WEEKLY_SYSTEM, json.dumps(payload, ensure_ascii=False),
        max_tokens=900, client_id=client_id, cost_category="engagement_weekly")

    stored = []
    for s in (result.get("suggestions") or [])[:MAX_SUGGESTIONS_PER_RUN]:
        title, body = (s.get("title") or "").strip(), (s.get("body") or "").strip()
        if not title or not body:
            continue
        kind = s.get("kind") if s.get("kind") in VALID_SUGGESTION_KINDS else "content_idea"
        _db().table("client_suggestions").insert({
            "client_id": client_id,
            "kind": kind,
            "title": title,
            "body": body,
            "source": s.get("source", "general"),
            "context": {"event_slug": s.get("event_slug", "")},
            "status": "pending",
        }).execute()
        stored.append(title)

    if stored:
        from agents.client_agent import log_activity, log_communication
        log_activity(client_id, AGENT_NAME, "suggestions_added",
                     {"count": len(stored), "titles": stored}, {})
        bullets = "\n".join(f"• {t}" for t in stored)
        log_communication(client_id, "outbound", "dashboard_chat",
                          f"הכנו לך {len(stored)} הצעות חדשות לשבוע הקרוב 🎯\n{bullets}\n"
                          'אפשר לאשר או לדחות כל אחת באזור "ממתין לאישור שלך" בדשבורד.')
    return len(stored)


def run_weekly_engagement() -> dict:
    """Weekly pass over every active client. Designed for a Cloud Scheduler
    hit on /api/engagement/weekly. One client failing never kills the run."""
    from agents.client_agent import list_clients
    clients = list_clients("active")
    events = israel_calendar.upcoming_events()
    log_step(AGENT_NAME, "weekly_engagement",
             f"{len(clients)} active clients, {len(events)} calendar events in window")

    staleness = israel_calendar.horizon_warning()
    if staleness:
        agent_alert(AGENT_NAME, [staleness])

    summary = {"clients": len(clients), "suggestions_created": 0, "failures": 0}
    for client in clients:
        try:
            summary["suggestions_created"] += timed_step(
                AGENT_NAME, f"client_{client['id']}",
                lambda c=client: _generate_for_client(c, events))
        except Exception as e:  # includes ClaudeJSONError — one client never kills the run
            summary["failures"] += 1
            agent_alert(AGENT_NAME, [f"weekly suggestions failed for client {client['id']}: {e}"])
    log_step(AGENT_NAME, "weekly_engagement",
             f"done — {summary['suggestions_created']} suggestions, {summary['failures']} failures")
    return summary


# ─── Daily sales alerts ───────────────────────────────────────────────────────

def _sales_alert_already_sent(client_id: int, date_key: str) -> bool:
    rows = (_db().table("client_activity").select("id")
            .eq("client_id", client_id).eq("agent_name", AGENT_NAME)
            .eq("action_type", "sales_alert_sent")
            .eq("details->>date", date_key).limit(1).execute().data)
    return bool(rows)


def run_daily_engagement() -> dict:
    """Yesterday's conversions per client → celebration email. Designed for a
    Cloud Scheduler hit on /api/engagement/daily (morning, after the ads
    platforms have settled yesterday's numbers)."""
    from agents.client_agent import list_clients, log_activity
    from agents.google_ads_agent import get_conversions_yesterday as google_conversions
    from agents.meta_ads_agent import get_conversions_yesterday as meta_conversions
    from core.email_service import send_sales_alert

    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    summary = {"clients_checked": 0, "alerts_sent": 0}
    for client in list_clients("active"):
        client_id = client["id"]
        summary["clients_checked"] += 1
        conversions = {}
        for platform, fetch in (("google_ads", google_conversions),
                                ("meta_ads", meta_conversions)):
            count = fetch(client_id)
            if count:
                conversions[PLATFORM_LABELS_HE[platform]] = count
        if not conversions or not client.get("email"):
            continue
        if _sales_alert_already_sent(client_id, yesterday):
            continue
        send_sales_alert(client["email"], client.get("name", ""), conversions, client_id)
        log_activity(client_id, AGENT_NAME, "sales_alert_sent",
                     {"date": yesterday, "conversions": conversions}, {})
        summary["alerts_sent"] += 1

    log_step(AGENT_NAME, "daily_engagement",
             f"done — {summary['alerts_sent']} sales alerts of {summary['clients_checked']} clients")
    return summary


# ─── Urgent notifications (WhatsApp SOS rung) ─────────────────────────────────

def notify_client_urgent(client_id: int, message_he: str) -> dict:
    """SOS ladder: WhatsApp to the client's phone; always also drops the
    message into their dashboard chat (so it exists somewhere they'll see
    even if WhatsApp fails); alerts the team only when a CONFIGURED WhatsApp
    send fails (unconfigured = expected during rollout, log-only)."""
    from agents.client_agent import get_client, log_activity, log_communication

    client = get_client(client_id)
    sent = send_whatsapp(client.get("phone", ""), message_he)
    try:
        log_communication(client_id, "outbound", "dashboard_chat", message_he)
    except Exception as e:
        print(f"[engagement_agent] chat fallback failed for client {client_id}: {e}")
    if not sent and whatsapp_configured():
        agent_alert(AGENT_NAME, [f"URGENT WhatsApp to client {client_id} FAILED — "
                                 f"message: {message_he[:120]}"])
    log_activity(client_id, AGENT_NAME, "urgent_notification",
                 {"channel": "whatsapp", "sent": sent}, {"message": message_he[:200]})
    return {"success": True, "whatsapp_sent": sent}


# ─── Login moments: proactive, LLM-WRITTEN, never templated (2026-08-02) ─────
# The fourth job. The three above are all SCHEDULED (cron hits an endpoint);
# this one is REACTIVE — it fires when the client actually shows up, which is
# the only moment we know they are reading.
#
# THE DESIGN RULE: triggers are evaluated in CODE (cheap DB reads, hard dedup),
# and only if something is genuinely worth saying does ONE LLM call write ONE
# message covering all of it. That split is deliberate:
# - Code decides WHETHER to speak, so an LLM can never invent a reason to.
# - The LLM decides HOW, so the message is specific to what actually happened
#   and does not become the same four sentences every morning. The existing
#   chat pushes in this file are f-string templates; those are one-off
#   notifications, this is a daily surface, and a daily template is how a
#   product starts to feel dead.
#
# Cost: at most one call per client per day (greeting dedup gates the whole
# thing), and zero calls when nothing is worth saying.

LOGIN_MOMENT_ACTION = "login_moment_sent"
FEEDBACK_ASK_ACTION = "feedback_asked"
FEEDBACK_ASK_INTERVAL_DAYS = 7

# Israel is the client base; the greeting must match THEIR clock, not the
# container's UTC. No pytz/zoneinfo dependency needed for a fixed offset
# question this coarse — and DST slippage of an hour cannot move "morning"
# into "evening".
ISRAEL_UTC_OFFSET_HOURS = 3


def _israel_hour() -> int:
    return (datetime.now(timezone.utc) + timedelta(hours=ISRAEL_UTC_OFFSET_HOURS)).hour


def _part_of_day() -> str:
    hour = _israel_hour()
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


def _acted_within(client_id: int, action_type: str, days: int) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = (_db().table("client_activity").select("id")
            .eq("client_id", client_id).eq("agent_name", AGENT_NAME)
            .eq("action_type", action_type)
            .gte("created_at", cutoff).limit(1).execute().data)
    return bool(rows)


def _collect_login_facts(client_id: int) -> dict:
    """What is TRUE right now and worth a mention. Pure reads, no LLM, no
    side effects — and every value here is something a row proves."""
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    facts = {"part_of_day": _part_of_day()}

    # (2.2) Their side of the work: approved homework still open, and anything
    # still pending a decision. "Pending" is what we ask about directly.
    pending = [s for s in get_suggestions(client_id, status="pending", limit=10)]
    facts["pending_suggestions"] = [
        {"title": s.get("title", ""), "kind": s.get("kind", "")} for s in pending[:4]]
    approved_homework = (_db().table("client_suggestions")
                         .select("title,decided_at,kind")
                         .eq("client_id", client_id).eq("kind", "homework")
                         .eq("status", "approved")
                         .order("decided_at", desc=True).limit(3).execute().data or [])
    facts["homework_in_progress"] = [h.get("title", "") for h in approved_homework]

    # (2.6) Things WE delivered since they last looked — praise has to name the
    # actual item or it is noise.
    delivered = (_db().table("client_activity")
                 .select("agent_name,action_type,details,created_at")
                 .eq("client_id", client_id)
                 .in_("action_type", ["media_image_created", "media_video_created",
                                      "seo_article_generated", "landing_page_published",
                                      "media_plan_proposed"])
                 .gte("created_at", since)
                 .order("created_at", desc=True).limit(5).execute().data or [])
    facts["recently_delivered"] = [
        {"what": d.get("action_type", ""),
         "detail": (d.get("details") or {}).get("title")
                   or (d.get("details") or {}).get("topic")
                   or (d.get("details") or {}).get("brief", "")[:80]}
        for d in delivered]

    # (2.3) New leads since a week ago, with whatever attribution we honestly
    # have. NOTE: client_leads.source is a CHANNEL (form/phone/whatsapp), not
    # organic-vs-paid — the utm/gclid capture that would make that distinction
    # real is not built yet, so the prompt is told not to claim a traffic
    # source. Congratulating on the lead itself is still true.
    leads = (_db().table("client_leads").select("name,source,source_detail,status,created_at")
             .eq("client_id", client_id).gte("created_at", since)
             .order("created_at", desc=True).limit(10).execute().data or [])
    facts["new_leads_this_week"] = len(leads)
    facts["new_won_this_week"] = sum(1 for row in leads if row.get("status") == "won")
    facts["lead_channels"] = sorted({row.get("source", "") for row in leads if row.get("source")})

    # (2.4) Weekly satisfaction check-in, at most once a week
    facts["ask_for_feedback"] = not _acted_within(client_id, FEEDBACK_ASK_ACTION,
                                                  FEEDBACK_ASK_INTERVAL_DAYS)
    return facts


LOGIN_MOMENT_SYSTEM = """You are the uallak team greeting an existing client who has just
opened their dashboard. Write ONE short message that feels like a person who actually looked
at their account before speaking — not a daily template.

You receive FACTS about what is genuinely true right now. Rules about them:
- Mention only what is in the facts. Never invent results, numbers, traffic sources, or
  work that isn't listed. If a fact list is empty, it did not happen.
- Greet for the given part_of_day, naturally — not "Good morning!" as a header.
- If `pending_suggestions` is non-empty, ASK DIRECTLY whether they've managed to prepare or
  decide on it, naming the specific item. Don't just list it.
- If `recently_delivered` is non-empty, acknowledge the SPECIFIC thing by name. "Well done"
  with nothing attached is worse than saying nothing.
- If `new_leads_this_week` > 0, be pleased about it and say the number. You may name the
  CHANNEL (`lead_channels`) but you must NOT say whether traffic was organic or paid — we
  do not measure that yet, and guessing would be a lie about their marketing.
- If `ask_for_feedback` is true, close by asking — warmly, once — whether they're happy with
  the platform so far and whether there's anything to pass to the team that builds it.
- If nothing above applies, a short warm greeting that offers help is the whole message.

TONE: one human on the client's team, mid-relationship. Never an AI or a bot. Never
enthusiastic beyond what the facts support.

LANGUAGE: {language_rule}

HARD LIMITS: max 4 sentences total. At most ONE question. No bullet lists. No emoji spam —
one, at most, and only if it fits.

Return JSON only:
{{"message": "the message", "asked_for_feedback": false}}"""


def run_login_moment(client_id: int, language: str = "he") -> dict:
    """Called when a client opens their dashboard. Returns
    {"message": str, "sent": bool} — empty message means nothing was worth
    saying, which is a normal and frequent outcome.

    Deduped to ONCE PER DAY per client: a client who reloads the dashboard six
    times gets greeted once. That dedup is also what caps the LLM spend.
    """
    if _acted_within(client_id, LOGIN_MOMENT_ACTION, 1):
        return {"success": True, "message": "", "sent": False, "reason": "already_greeted_today"}

    try:
        facts = _collect_login_facts(client_id)
    except Exception as e:
        # A greeting is a nicety; never let it break a dashboard load.
        print(f"[{AGENT_NAME}] login facts failed for client {client_id}: {e}")
        return {"success": False, "message": "", "sent": False}

    from agents.client_agent import get_client, log_activity, log_communication
    from agents.onboarding_agent import LANGUAGE_RULE

    client = get_client(client_id) or {}
    payload = {"client_name": client.get("name", ""), "ui_language": language, **facts}
    try:
        result = timed_step(
            AGENT_NAME, "login_moment_llm",
            lambda: safe_claude_json_call(
                LOGIN_MOMENT_SYSTEM.format(language_rule=LANGUAGE_RULE),
                json.dumps(payload, ensure_ascii=False),
                max_tokens=500, client_id=client_id, cost_category="engagement_login"))
    except Exception as e:  # includes ClaudeJSONError
        print(f"[{AGENT_NAME}] login moment generation failed for client {client_id}: {e}")
        return {"success": False, "message": "", "sent": False}

    message = (result.get("message") or "").strip()
    if not message:
        return {"success": True, "message": "", "sent": False}

    log_communication(client_id, "outbound", "dashboard_chat", message)
    log_activity(client_id, AGENT_NAME, LOGIN_MOMENT_ACTION,
                 {"part_of_day": facts["part_of_day"],
                  "pending": len(facts["pending_suggestions"]),
                  "delivered": len(facts["recently_delivered"]),
                  "new_leads": facts["new_leads_this_week"]}, {})
    if result.get("asked_for_feedback") and facts["ask_for_feedback"]:
        # Recorded separately so the weekly cadence is driven by when we
        # actually ASKED, not by when we happened to greet.
        log_activity(client_id, AGENT_NAME, FEEDBACK_ASK_ACTION, {}, {})
    log_step(AGENT_NAME, "login_moment",
             f"client {client_id}: greeted ({facts['part_of_day']})")
    return {"success": True, "message": message, "sent": True,
            "asked_for_feedback": bool(result.get("asked_for_feedback"))}


# ─── Client feedback on uallak itself (the weekly ask's answer) ──────────────

def store_feedback(client_id: int, message: str, rating=None,
                   source: str = "weekly_checkin") -> dict:
    """Persist what the client said about the PLATFORM (not their business).
    Johnny reads these; see migrations/2026-08-02-client-feedback.sql for why
    this is the one thing here that earns its own table."""
    text = (message or "").strip()[:4000]
    if not text and rating is None:
        return {"success": False, "error": "empty feedback"}
    try:
        row = _db().table("client_feedback").insert({
            "client_id": client_id,
            "rating": int(rating) if str(rating).strip().isdigit() else None,
            "message": text,
            "source": source,
        }).execute()
    except Exception as e:
        # Before the migration runs this is the expected path — the ASK still
        # happened, so losing the answer silently would be the worst outcome.
        agent_alert(AGENT_NAME, [f"client {client_id}: could not STORE platform feedback "
                                 f"({e}). The client's words were: {text[:400]}"])
        return {"success": False, "error": str(e)}

    from agents.client_agent import log_activity
    log_activity(client_id, AGENT_NAME, "feedback_received",
                 {"rating": rating, "chars": len(text)}, {})
    agent_alert(AGENT_NAME, [f"client {client_id} left platform feedback"
                             f"{f' (rating {rating}/5)' if rating else ''}: {text[:300]}"])
    return {"success": True, "id": (row.data or [{}])[0].get("id")}


def list_feedback(limit: int = 100, only_unreviewed: bool = False) -> list:
    """Johnny's review list, newest first."""
    query = (_db().table("client_feedback").select("*")
             .order("created_at", desc=True).limit(limit))
    if only_unreviewed:
        query = query.eq("reviewed", False)
    return query.execute().data or []


def mark_feedback_reviewed(feedback_id: int) -> dict:
    result = (_db().table("client_feedback").update({"reviewed": True})
              .eq("id", feedback_id).execute())
    return {"success": bool(result.data)}


PAYMENT_FAILURE_MESSAGE_HE = (
    "⚠️ עדכון חשוב מ-uallak: ניסיון החיוב האחרון לא עבר (כרטיס או חשבון PayPal). "
    "כדי שהשירות ימשיך לרוץ בלי הפרעה, כדאי לבדוק את אמצעי התשלום - "
    "ואם צריך עזרה, אנחנו זמינים בצ'אט בדשבורד."
)


def notify_payment_failure(client_id: int, event_type: str) -> dict:
    """Failed purchase/checkout charge (PayPal webhook: payment failed /
    denied / subscription suspended) — a lost-sale moment, so it rides the
    SOS rung. Deduped per calendar day because PayPal retries failed charges
    and each retry fires the webhook again.

    Today this covers the only checkout in the system (uallak's own PayPal
    flow). When client-webshop e-commerce integration exists (WooCommerce —
    deferred), its failed-checkout events must call THIS function, not grow
    a parallel notification path."""
    from agents.client_agent import log_activity

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = (_db().table("client_activity").select("id")
            .eq("client_id", client_id).eq("agent_name", AGENT_NAME)
            .eq("action_type", "payment_failure_notified")
            .eq("details->>date", today).limit(1).execute().data)
    if rows:
        return {"success": True, "skipped": "already notified today"}

    # The team hears about it too — a failing charge often ends in churn
    agent_alert(AGENT_NAME, [f"client {client_id}: PayPal payment failure "
                             f"({event_type}) — lost-sale moment, follow up"])
    result = notify_client_urgent(client_id, PAYMENT_FAILURE_MESSAGE_HE)
    log_activity(client_id, AGENT_NAME, "payment_failure_notified",
                 {"date": today, "event_type": event_type}, {})
    return result
