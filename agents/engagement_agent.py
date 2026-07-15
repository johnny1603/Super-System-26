"""uallak's proactive engagement engine — the shift from "answer when asked"
to "drive the relationship". Three jobs:

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


def decide_suggestion(client_id: int, suggestion_id: int, decision: str) -> dict:
    """Client taps approve/reject in the dashboard. Ownership-checked (the
    row must belong to the session's client) and only pending rows move."""
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
        # v1 fulfillment is human — the team must actually see approvals
        agent_alert(AGENT_NAME, [f"client {client_id} APPROVED suggestion "
                                 f"'{suggestion.get('title', '')}' (#{suggestion_id}) — action needed"])
    return {"success": True, "id": suggestion_id, "status": decision}


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
        send_sales_alert(client["email"], client.get("name", ""), conversions)
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
