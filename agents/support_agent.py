"""uallak's client-facing support chat agent.

Answers questions from already-paying clients in their dashboard chat panel,
grounded in their own package/proposal data and recent account activity.
This is not the sales chat (agents/onboarding_agent.py) - these clients
already paid and are asking about their account.
"""
import json
import os

from supabase import create_client as _supabase_client

from agents.client_agent import get_client, get_activity
from core.agent_base import agent_alert, log_step, timed_step
from core.claude_json import ClaudeJSONError, safe_claude_json_call

AGENT_NAME = "support_agent"

# Created lazily - no DB client at import time (api_server imports every agent at startup)
_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        _db_instance = _supabase_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
    return _db_instance


def consult_platform_agent(client_id: int, platform: str, question: str):
    """Platform-specific live data for support answers. Google Ads and Meta are
    real now (via the accounts the client connected in the dashboard); TikTok
    still pending its execution agent."""
    if platform in ("google", "google_ads"):
        from agents.google_ads_agent import get_campaign_performance
        performance = get_campaign_performance(client_id)
        if performance.get("connected"):
            return performance
        return "חשבון Google Ads עדיין לא מחובר - אפשר לחבר אותו בלחיצה על 'חבר עכשיו' בדשבורד"
    if platform in ("meta", "meta_ads", "facebook", "instagram"):
        from agents.meta_ads_agent import get_campaign_performance
        performance = get_campaign_performance(client_id)
        if performance.get("connected"):
            return performance
        return "חשבון Meta עדיין לא מחובר - אפשר לחבר אותו בלחיצה על 'חבר עכשיו' בדשבורד"
    if platform in ("website", "wordpress", "site"):
        from agents.website_agent import get_site_overview
        overview = get_site_overview(client_id)
        if overview.get("connected"):
            return overview
        return "האתר עדיין לא מחובר - אפשר לחבר אותו בכרטיס 'האתר שלך' בדשבורד"
    return f"אין עדיין חיבור לנתונים בזמן אמת של {platform} - זה יתווסף בקרוב"


def _latest_proposal(email: str) -> dict:
    """The client's actual proposal (packages, pricing) lives on their lead row,
    matched by email - clients/leads aren't linked by id."""
    if not email:
        return {}
    result = (
        _db().table("leads")
        .select("proposal")
        .eq("client_email", email)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return (result.data[0].get("proposal") or {}) if result.data else {}


SYSTEM = """You are uallak's professional support assistant, chatting with an existing,
already-paying client inside their private dashboard. You are presented as "the uallak
team" - never say you are an AI or a bot.

Answer questions about their package, pricing, and how the system works, using ONLY the
client data given to you in the user message (their package/proposal details and recent
account activity). Never invent numbers, features, or activity not present in that data.

Platform data: if the user message includes a "google_ads_performance" or
"meta_ads_performance" field, that is REAL live data from the client's own ad account
(last 30 days; "cost" values are in the account's currency, ILS for our clients;
meta_ads_performance covers Facebook + Instagram combined) - use it to answer campaign
questions concretely, citing the actual numbers. If it contains an "error" field, say there
was a temporary issue reading their campaign data and the team is on it. If a platform's
field is absent and the question needs that platform's data, that account isn't connected
yet - warmly point them to the "חבר עכשיו" button in the dashboard. TikTok has no live
connection yet; if asked about it, say so honestly and mention it's coming soon. Never
invent campaign numbers.

Website data: a "website_overview" field is REAL live data from the client's connected
WordPress site (site name, recent posts/pages and their statuses, whether an SEO plugin is
installed) - use it for "what's happening with my site" questions, citing actual titles and
statuses only. Same rules as ad platforms: "error" field means a temporary read issue; no
field means the site isn't connected yet - point them to the "האתר שלך" card in the dashboard.

If the question is genuinely unclear, unrelated to their account, or something you can't
answer confidently from the given data (this is different from "no platform data yet") -
say so honestly, tell the client a team member will follow up personally, and set
needs_human_followup to true.

Keep replies short: 2-4 sentences max. Hebrew only.

Return JSON only:
{"reply": "Hebrew text", "needs_human_followup": true/false}"""

_FALLBACK = {
    "reply": "מצטערים, הייתה תקלה קטנה מהצד שלנו - צוות uallak יחזור אליך בהקדם 🙏",
    "needs_human_followup": True,
}


def answer_support_question(client_id: int, message: str) -> dict:
    log_step(AGENT_NAME, "answer_support_question", f"client_id={client_id}")

    client = get_client(client_id)
    payload = {
        "client": {
            "name": client.get("name"),
            "package": client.get("package"),
            "status": client.get("status"),
        },
        "proposal": _latest_proposal(client.get("email", "")),
        "recent_activity": get_activity(client_id, limit=15),
        "client_message": message,
    }

    # Real campaign data when the client has connected an ad platform. Cheap
    # enough to always include: the agents cache results for 5 minutes, and it
    # means "how are my campaigns doing?" gets actual numbers, not a canned line.
    from agents.google_ads_agent import get_campaign_performance as google_performance
    from agents.meta_ads_agent import get_campaign_performance as meta_performance
    from agents.website_agent import get_site_overview as website_overview
    for field, fetch in (("google_ads_performance", google_performance),
                         ("meta_ads_performance", meta_performance),
                         ("website_overview", website_overview)):
        performance = fetch(client_id)
        if performance.get("connected"):
            payload[field] = performance

    user_message = json.dumps(payload, ensure_ascii=False)

    try:
        result = timed_step(
            AGENT_NAME, "llm_call",
            lambda: safe_claude_json_call(SYSTEM, user_message, max_tokens=800,
                                          client_id=client_id, cost_category="claude_support"),
        )
    except ClaudeJSONError as e:
        agent_alert(AGENT_NAME, [f"answer_support_question failed for client {client_id}: {e}"])
        return _FALLBACK

    reply = result.get("reply", "")
    needs_human_followup = bool(result.get("needs_human_followup", False))
    if needs_human_followup:
        agent_alert(AGENT_NAME, [f'client {client_id} needs human follow-up: "{message}"'])

    return {"reply": reply, "needs_human_followup": needs_human_followup}
