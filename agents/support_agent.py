"""uallak's client-facing support chat agent.

Answers questions from already-paying clients in their dashboard chat panel,
grounded in their own package/proposal data and recent account activity.
This is not the sales chat (agents/onboarding_agent.py) - these clients
already paid and are asking about their account.

Two-stage answering: the main JSON call answers from account/platform context;
when it decides a business-relevant question genuinely needs CURRENT external
information, it emits a web_search_query and a second, text-mode call with
Anthropic's server-side web_search tool produces the final reply (see
claude_web_search_call in core/claude_json.py for why that's a separate path).
"""
import json
import os
from datetime import datetime

from supabase import create_client as _supabase_client

from agents.client_agent import get_client, get_activity, get_communications, log_activity
from agents.onboarding_agent import LANGUAGE_RULE
from core.agent_base import agent_alert, log_step, timed_step
from core.claude_json import ClaudeJSONError, claude_web_search_call, safe_claude_json_call

CONVERSATION_HISTORY_LIMIT = 12  # current-thread turns passed to the LLM
PENDING_SUGGESTIONS_LIMIT = 5

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


def _latest_lead(email: str) -> dict:
    """The client's original sales-chat record (answers + proposal) lives on
    their lead row, matched by email - clients/leads aren't linked by id. The
    answers feed upgrade proposals; the proposal feeds everyday questions."""
    if not email:
        return {}
    result = (
        _db().table("leads")
        .select("answers,proposal")
        .eq("client_email", email)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else {}


def _current_thread(client_id: int, chat_started_at: str) -> list:
    """The current conversation thread (oldest first) for LLM context. The
    thread boundary is clients.chat_started_at - set by the dashboard's
    'שיחה חדשה' action; None/empty means the whole history is one thread."""
    rows = get_communications(client_id, limit=CONVERSATION_HISTORY_LIMIT * 2,
                              channel="dashboard_chat")
    if chat_started_at:
        # Both timestamps come from Postgres in the same ISO form, so string
        # comparison is safe and avoids format-parsing pitfalls
        rows = [r for r in rows if (r.get("created_at") or "") >= chat_started_at]
    rows = list(reversed(rows))[-CONVERSATION_HISTORY_LIMIT:]
    return [{"from": "client" if r.get("direction") == "inbound" else "uallak",
             "text": (r.get("content") or "")[:400]} for r in rows]


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

Conversation: "conversation_history" holds the current chat thread (oldest first). Use it to
resolve references ("כן", "תוסיפו גם את זה", "כמה זה יעלה?") and never re-ask something the
client already told you in this thread.

Pending suggestions: "pending_suggestions" lists items waiting for the client's approval in
the dashboard's "ממתין לאישור שלך" area (the team's weekly homework/ideas for them). When the
client asks what's waiting, what's new, or what they should do - walk them through the items
warmly by title, explain that approving is what lets the team start executing, and point them
to approve/reject each one in that area. Be a concierge driving action, not a card reader.

Upgrades: when the client shows GENUINE purchase/upgrade intent - asking to add a platform or
service, expand scope, or get pricing for more work (not casual curiosity; if unsure, ask ONE
clarifying question first instead) - set "upgrade_request" to a short ENGLISH summary of exactly
what they want (services, budgets, constraints - pull specifics from conversation_history), and
put in "reply" a short holding line ("רגע, מכינים לך הצעה מסודרת 🙏") used only if the build
fails. The system then builds a real, priced proposal using the same pricing brain as
onboarding. When "upgrade_request" is set, "web_search_query" must be "".

Web search: for BUSINESS/MARKETING-RELEVANT questions that genuinely need current or
external information you don't have - e.g. "מה אומר העדכון החדש של גוגל", "כמה עולה בדרך
כלל X בשוק", an upcoming commercial moment, a competitor/industry fact - set
"web_search_query" to ONE focused search query (Hebrew or English), and put in "reply" a
short honest holding line (e.g. "שנייה, בודקים את זה לעומק") that is used only if the
search fails. Do NOT set web_search_query for: anything answerable from the given account
data or general knowledge; anything about THEIR campaigns/site/billing (that data is
already provided or the account isn't connected); or questions unrelated to their business
and marketing (news, politics, homework) - for those keep the honest behavior above. Most
questions need no search - leave web_search_query as "".

If the question is genuinely unclear, unrelated to their account, or something you can't
answer confidently from the given data (this is different from "no platform data yet" and
from "needs a web search") - say so honestly, tell the client a team member will follow up
personally, and set needs_human_followup to true.

Keep replies short: 2-4 sentences max, in the client's language (the language of their message
and conversation_history — see CLIENT LANGUAGE; Hebrew default).

Return JSON only:
{"reply": "client-language text", "needs_human_followup": true/false, "web_search_query": "",
 "upgrade_request": ""}""" + LANGUAGE_RULE

SEARCH_SYSTEM = """You are uallak's professional support assistant, chatting with an existing,
already-paying client inside their private dashboard. You are presented as "the uallak
team" - never say you are an AI or a bot.

The client asked something that needs current, real-world information beyond their account
data. Use web search to find it, then answer them directly.

Rules:
- Run only the searches needed to answer (1-3 focused searches), then answer from the results.
- Facts, prices, dates, and claims must come from the search results - never invent them.
  Phrase numbers as approximations/ranges, the way a professional consultant would.
- You are a marketing agency: when natural, connect the answer back to the client's own
  business and marketing (their business context is provided).
- If the search doesn't produce a reliable answer, say so honestly and that a team member
  will follow up personally - never bluff.
- Reply in the client's language (the language their message is written in; Hebrew default -
  supported: Hebrew, English, French, Arabic, Russian), 2-5 sentences, PLAIN TEXT (no JSON, no
  markdown headers, no link lists; mentioning a source naturally inline, like "לפי נתוני...",
  is fine)."""

_FALLBACK = {
    "reply": "מצטערים, הייתה תקלה קטנה מהצד שלנו - צוות uallak יחזור אליך בהקדם 🙏",
    "needs_human_followup": True,
}


# ─── In-chat upgrade proposals (reuses the onboarding pricing brain) ──────────

def _current_monthly_fee(client_id: int) -> int:
    """What the client actually pays now, from the checkout activity row -
    same derivation the dashboard billing section uses."""
    for entry in get_activity(client_id, limit=100):
        if (entry.get("agent_name") == "paypal_service"
                and entry.get("action_type") == "subscription_created"):
            return int((entry.get("details") or {}).get("monthly_management_total") or 0)
    return 0


def _format_proposal_reply(proposal: dict) -> str:
    """Deterministic, compact Hebrew presentation of a proposal for the chat -
    no extra LLM round-trip on top of the (already slow) build."""
    lines = ["הכנו לך הצעה מסודרת 👇"]
    for pkg in (proposal.get("packages") or [])[:2]:
        monthly = int(pkg.get("monthly_management_total", 0) or 0)
        setup = int(pkg.get("setup_fee_total", 0) or 0)
        line = f"• {pkg.get('name', '')} — ₪{monthly:,}/חודש"
        if setup:
            line += f" + ₪{setup:,} הקמה חד-פעמית"
        lines.append(line)
        if pkg.get("description"):
            lines.append(f"   {pkg['description']}")
    if proposal.get("honest_note"):
        lines.append(proposal["honest_note"])
    lines.append("רוצים להתקדם עם אחת מהאפשרויות? כתבו לי כאן, והצוות יסגור איתכם את הפרטים והעדכון ייכנס לתוקף בחיוב הבא.")
    return "\n".join(lines)


def _build_upgrade_proposal(client_id: int, client: dict, lead: dict,
                            upgrade_request: str) -> str:
    """Build a real upgrade proposal through onboarding's build_proposal
    (upgrade mode) + the numeric QA pass, record it as a lead row (same place
    original proposals live), alert the team, and return the chat reply.
    The content-QA LLM pass is deliberately skipped here - it would double an
    already ~minute-long chat response; the numeric invariants still run."""
    from agents.onboarding_agent import build_proposal, get_api_key
    from agents.qa_agent import qa_check

    answers = dict(lead.get("answers") or {})
    upgrade_context = {
        "current_package": client.get("package", ""),
        "current_monthly_fee_ils": _current_monthly_fee(client_id),
        "upgrade_request": upgrade_request,
    }
    proposal = build_proposal(answers, get_api_key(), upgrade_context=upgrade_context)
    proposal = qa_check(proposal, answers)

    packages = proposal.get("packages") or []
    if not proposal.get("approved", True) or not packages:
        raise RuntimeError(f"upgrade proposal came back empty/unapproved "
                           f"(reason: {proposal.get('rejection_reason', '')})")

    # Same record-keeping as onboarding proposals - the admin lead view is the
    # single place proposals live
    cheapest = min(packages, key=lambda p: p.get("monthly_management_total", 0))
    _db().table("leads").insert({
        "created_at": datetime.now().isoformat(),  # leads has no default (onboarding sets it too)
        "client_email": client.get("email", ""),
        "client_name": client.get("name", ""),
        "answers": {**answers, "_upgrade_request": upgrade_request},
        "proposal": proposal,
        "approved": bool(proposal.get("approved")),
        "setup_fee": cheapest.get("setup_fee_total", 0),
        "monthly_fee": cheapest.get("monthly_management_total", 0),
    }).execute()
    log_activity(client_id, AGENT_NAME, "upgrade_proposal_built",
                 {"request": upgrade_request},
                 {"packages": [{"name": p.get("name"), "monthly": p.get("monthly_management_total"),
                                "setup": p.get("setup_fee_total")} for p in packages]})
    agent_alert(AGENT_NAME, [f"client {client_id} got an in-chat UPGRADE proposal "
                             f"({upgrade_request}) — follow up to close and adjust billing"])
    return _format_proposal_reply(proposal)


def answer_support_question(client_id: int, message: str) -> dict:
    log_step(AGENT_NAME, "answer_support_question", f"client_id={client_id}")

    client = get_client(client_id)
    lead = _latest_lead(client.get("email", ""))
    payload = {
        "client": {
            "name": client.get("name"),
            "package": client.get("package"),
            "status": client.get("status"),
        },
        "proposal": lead.get("proposal") or {},
        "recent_activity": get_activity(client_id, limit=15),
        "conversation_history": _current_thread(client_id, client.get("chat_started_at")),
        "client_message": message,
    }

    # The chat owns the homework/suggestions experience - it must know what's
    # pending so it can walk the client through approving it
    try:
        from agents.engagement_agent import get_suggestions
        pending = get_suggestions(client_id, status="pending",
                                  limit=PENDING_SUGGESTIONS_LIMIT)
        if pending:
            payload["pending_suggestions"] = [
                {"title": s.get("title", ""), "kind": s.get("kind", "")} for s in pending]
    except Exception as e:
        log_step(AGENT_NAME, "platform_context",
                 f"client {client_id}: suggestions fetch failed (degrading): {e}")

    # Real campaign data when the client has connected an ad platform. Cheap
    # enough to always include: the agents cache results for 5 minutes, and it
    # means "how are my campaigns doing?" gets actual numbers, not a canned line.
    from agents.google_ads_agent import get_campaign_performance as google_performance
    from agents.meta_ads_agent import get_campaign_performance as meta_performance
    from agents.website_agent import get_site_overview as website_overview
    for field, fetch in (("google_ads_performance", google_performance),
                         ("meta_ads_performance", meta_performance),
                         ("website_overview", website_overview)):
        # One broken integration must never take down the whole chat - a
        # failed getter degrades to "no data for that platform" (the prompt
        # already handles an absent field). The created_at 500 of 2026-07-16
        # is exactly the failure mode this guards against.
        try:
            performance = fetch(client_id)
        except Exception as e:
            log_step(AGENT_NAME, "platform_context",
                     f"client {client_id}: {field} fetch failed (degrading to no data): {e}")
            continue
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

    # Upgrade stage: genuine purchase/upgrade intent detected - build a real
    # priced proposal with the SAME brain as onboarding (build_proposal in
    # upgrade mode), so an existing client gets the same quality of proposal
    # a new client gets. Takes priority over web search.
    upgrade_request = (result.get("upgrade_request") or "").strip()[:400]
    if upgrade_request:
        log_step(AGENT_NAME, "upgrade_proposal", f"client {client_id}: '{upgrade_request}'")
        try:
            reply = timed_step(
                AGENT_NAME, "upgrade_proposal_build",
                lambda: _build_upgrade_proposal(client_id, client, lead, upgrade_request))
            needs_human_followup = False  # the dedicated alert below covers the team
        except Exception as e:
            log_step(AGENT_NAME, "upgrade_proposal", f"client {client_id}: build failed ({e})")
            agent_alert(AGENT_NAME, [f"in-chat upgrade proposal FAILED for client {client_id} "
                                     f"(request: {upgrade_request}): {e}"])
            needs_human_followup = True
            if not reply:
                return _FALLBACK
        if needs_human_followup:
            agent_alert(AGENT_NAME, [f'client {client_id} needs human follow-up: "{message}"'])
        return {"reply": reply, "needs_human_followup": needs_human_followup}

    # Stage 2: the JSON model decided this needs live web information (the
    # prompt gates this to business/marketing-relevant questions only). This
    # runs on a separate TEXT-mode path - search citations don't mix with
    # strict JSON output - so the JSON contract above stays untouched.
    search_query = (result.get("web_search_query") or "").strip()[:200]
    if search_query:
        log_step(AGENT_NAME, "web_search", f"client {client_id}: '{search_query}'")
        search_payload = json.dumps({
            "client_business": payload["client"],
            "business_summary": (payload.get("proposal") or {}).get("business_summary", ""),
            "client_message": message,
            "search_focus": search_query,
        }, ensure_ascii=False)
        try:
            reply = timed_step(
                AGENT_NAME, "web_search_call",
                lambda: claude_web_search_call(SEARCH_SYSTEM, search_payload, max_tokens=1000,
                                               client_id=client_id,
                                               cost_category="claude_support_search"),
            )
            needs_human_followup = False
        except Exception as e:
            # Fall back to stage 1's holding reply; a human should still close
            # the loop on the question the search was meant to answer
            log_step(AGENT_NAME, "web_search", f"client {client_id}: search failed ({e})")
            needs_human_followup = True
            if not reply:
                agent_alert(AGENT_NAME, [f"support web search failed for client {client_id}: {e}"])
                return _FALLBACK

    if needs_human_followup:
        agent_alert(AGENT_NAME, [f'client {client_id} needs human follow-up: "{message}"'])

    return {"reply": reply, "needs_human_followup": needs_human_followup}
