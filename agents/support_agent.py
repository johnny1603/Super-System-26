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

SPECIALIST PERSONAS (2026-07-23): alongside the general concierge, the client
can talk to four platform specialists (Google Ads / Meta / Website+SEO /
Media+Content) chosen via the chat's team strip. THE STRUCTURAL SAFETY
BOUNDARY: answer_persona_question() is a READ-ONLY path by construction —
the only platform functions it can reach are the read/informational getters
whitelisted in PERSONAS[..]["reads"], and its JSON contract has NO
upgrade_request / web_search_query / any action field; unknown keys the model
might emit are simply never read. A persona literally has no code path to
pause/resume/create campaigns, change budgets, publish content, or trigger a
proposal build — asking nicely (or adversarially) cannot change that. Adding
an action-capable function to a persona's reads is a bug by definition; keep
this invariant when extending.
"""
import json
import os
from datetime import datetime

from supabase import create_client as _supabase_client

from agents.client_agent import get_client, get_activity, get_communications, log_activity
from agents.onboarding_agent import LANGUAGE_RULE
from core import automation_service
from core.agent_base import agent_alert, log_step, timed_step
from core import chat_fallback
from core.claude_json import claude_web_search_call, safe_claude_json_call

CONVERSATION_HISTORY_LIMIT = 12  # current-thread turns passed to the LLM
PENDING_SUGGESTIONS_LIMIT = 5

AGENT_NAME = "support_agent"

# Marketing automation is the ONE service the pricing brain must never touch:
# it has no fixed price, so routing it to upgrade_request would produce an
# invented number in a real proposal. The vendor names and the signup wording
# come from core/automation_service.py so there is one copy of both.
_AUTOMATION_RULE = f"""

MARKETING AUTOMATION ({" / ".join(v["name"] for v in automation_service.VENDORS.values())}) —
THE ONE THING YOU NEVER PRICE AND NEVER SEND TO THE PROPOSAL BUILDER:
{automation_service.POLICY_NOTE}
- NEVER set "upgrade_request" for an automation question, no matter how clearly the client asks
  to buy it. The proposal builder would invent a price for something that is quoted by hand.
  Answer in "reply" instead
- What you DO: explain in plain words what it could do for their specific business, then say the
  next step is a short conversation with the team on WhatsApp to work out what they actually
  need — the button on the automation card in their dashboard opens it. Warm and concrete, not a
  brush-off
- If the client says they want to go ahead, give them exactly two steps: (1) open their own
  account at the vendor, and (2) send us the username and password so we can build it for them.
  With step (1), always include this instruction, in their language, keeping this substance:
  "{automation_service.SIGNUP_RULE_HE}"
- Never ask them to type credentials into the dashboard — there is no field for it and there
  never will be; the details are handed to the team in the conversation
- If they ask "how much", say honestly that it depends on what they need and that the team will
  price it personally after the short conversation. Do not guess a range"""

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


def persona_channel(persona_id: str) -> str:
    """Each specialist persona keeps its own conversation, fully separate from
    the general concierge and from every other specialist. We encode that
    separation in the communications 'channel': the general concierge stays
    'dashboard_chat' (unchanged, so existing history stays intact), and each
    specialist uses 'dashboard_chat:<persona_id>'. One column, no migration —
    and the LLM for a given persona only ever sees that persona's own thread."""
    pid = (persona_id or "general").strip()
    return "dashboard_chat" if pid == "general" else f"dashboard_chat:{pid}"


def _current_thread(client_id: int, chat_started_at: str,
                    channel: str = "dashboard_chat") -> list:
    """The current conversation thread (oldest first) for LLM context, scoped
    to ONE channel (the general concierge or a single specialist persona — see
    persona_channel). The thread boundary is clients.chat_started_at - set by
    the dashboard's 'שיחה חדשה' action; None/empty means the whole history is
    one thread."""
    rows = get_communications(client_id, limit=CONVERSATION_HISTORY_LIMIT * 2,
                              channel=channel)
    if chat_started_at:
        # Both timestamps come from Postgres in the same ISO form, so string
        # comparison is safe and avoids format-parsing pitfalls
        rows = [r for r in rows if (r.get("created_at") or "") >= chat_started_at]
    rows = list(reversed(rows))[-CONVERSATION_HISTORY_LIMIT:]
    return [{"from": "client" if r.get("direction") == "inbound" else "uallak",
             "text": (r.get("content") or "")[:400]} for r in rows]


# Shared by the concierge and every specialist persona (see core/chat_fallback
# for the house rule). Brace-free on purpose: it is concatenated into
# PERSONA_SYSTEM_TEMPLATE before .format(). Defined before SYSTEM, which uses it.
HANDOFF_RULE = """

WHEN YOU CAN'T ANSWER — NEVER A DEAD END, ALWAYS IN YOUR OWN VOICE:
- OUT OF SCOPE: if the question is outside what uallak can responsibly answer at all — legal,
  tax/accounting, medical, insurance, contracts, anything that needs a licensed professional, or
  a decision only the team can make (refunds, exceptions to the package, promises about results)
  — do NOT attempt an answer and never bluff. Say so honestly in one short line, the way you
  normally talk, that this isn't something you can help with directly and you're passing it to
  the team who will get back to them (for example: "זה לא בתחום שאני יכול לעזור בו ישירות —
  אני מעביר את זה לצוות ויחזרו אליך", in your own grammatical gender and the client's
  language), then offer to keep going with what you CAN help with. Set "out_of_scope" to true
  AND "needs_human_followup" to true — that is what actually sends it to the team.
- DATA YOU COULDN'T READ: if a data field you would need carries an "error", say plainly and
  warmly that you're having trouble pulling it up right now and the team is already checking
  (for example: "אני נתקל בקושי לשלוף את זה כרגע, אני כבר מעביר לצוות שיבדקו") — never error
  codes, never "system error" / "תקלה טכנית" wording — and carry on with whatever else you can.
- MOVE ON: name the limitation once. Don't apologize repeatedly, and if conversation_history
  shows something was already handed to the team, don't raise it again unless the client does —
  just keep being useful."""

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
onboarding - this also covers the avatar/digital-twin video add-on (a real person's likeness on
video, via HeyGen/ElevenLabs) when a client asks about it by name or describes wanting a digital
version of themselves/their team presenting content; the pricing brain decides whether it's
actually a good fit for their business, same as any other upgrade - never promise it yourself or
quote a number in "reply" beyond the holding line. When "upgrade_request" is set,
"web_search_query" must be "".

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
{"reply": "client-language text", "needs_human_followup": true/false, "out_of_scope": true/false,
 "web_search_query": "", "upgrade_request": ""}""" + HANDOFF_RULE + LANGUAGE_RULE + _AUTOMATION_RULE

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

# ─── Graceful failure + handoff (core/chat_fallback — no dead-end replies) ────
# The old _FALLBACK ("מצטערים, הייתה תקלה קטנה מהצד שלנו") is gone: it was the
# same line in every voice, and it only fired for unparseable JSON — an API
# timeout or overload sailed past `except ClaudeJSONError` and came back to the
# client as a bare 500.

def _client_language(client_id: int, message: str) -> str:
    try:
        stored = (get_client(client_id) or {}).get("language") or "he"
    except Exception:
        stored = "he"
    return chat_fallback.detect_language(message, stored)


def _technical_failure(client_id: int, persona_id: str, message: str, stage: str,
                       error: Exception) -> dict:
    """Something actually broke mid-turn. Fixed in-lane reply (there is no
    model to write one), a deduped alert carrying the client's question so a
    human can answer it, and the conversation carries on."""
    log_step(AGENT_NAME, "technical_failure",
             f"client {client_id} ({persona_id}) at {stage}: {type(error).__name__}: {error}")
    chat_fallback.alert_handoff(
        chat_fallback.KIND_TECHNICAL, AGENT_NAME,
        f'{stage} failed ({type(error).__name__}: {str(error)[:200]}). '
        f'Client asked: "{message[:300]}"',
        client_id=client_id, persona=persona_id,
        issue_key=f"technical:{persona_id}:{stage}:{chat_fallback.question_key(message)}")
    return {"reply": chat_fallback.technical_reply(persona_id, _client_language(client_id, message)),
            "needs_human_followup": True}


def _handoff_alert(client_id: int, persona_id: str, message: str, out_of_scope: bool):
    """The model answered in its own voice and flagged a handoff — route it."""
    kind = chat_fallback.KIND_OUT_OF_SCOPE if out_of_scope else chat_fallback.KIND_FOLLOWUP
    chat_fallback.alert_handoff(kind, AGENT_NAME, f'client asked: "{message[:400]}"',
                                client_id=client_id, persona=persona_id)


def _degraded_reads(payload: dict) -> list:
    """Data fields that came back carrying an error. The model tells the client
    it's having trouble pulling them (HANDOFF_RULE); this makes that true."""
    return [k for k, v in payload.items() if isinstance(v, dict) and v.get("error")]


def _alert_degraded(client_id: int, persona_id: str, fields: list):
    for field in fields:
        chat_fallback.alert_handoff(
            chat_fallback.KIND_TECHNICAL, AGENT_NAME,
            f"{field} could not be read during a chat turn — the client was told the team is checking",
            client_id=client_id, persona=persona_id,
            issue_key=f"technical:{persona_id}:read:{field}")


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
    """The concierge turn. NEVER raises and never returns an empty reply —
    anything that breaks becomes an in-voice technical handoff (see
    core/chat_fallback), so the chat always gets something it can show."""
    try:
        result = _answer_support_question(client_id, message)
    except Exception as e:
        return _technical_failure(client_id, "general", message, "support_turn", e)
    if not (result.get("reply") or "").strip():
        return _technical_failure(client_id, "general", message, "support_turn",
                                  RuntimeError("model returned an empty reply"))
    return result


def _answer_support_question(client_id: int, message: str) -> dict:
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
                     f"client {client_id}: {field} fetch failed (degrading): {e}")
            # NOT dropped: an absent field reads to the model as "not connected
            # — go press חבר עכשיו", which is wrong for a connected account
            # whose read just failed. The error marker makes the model say it's
            # having trouble pulling it, and _alert_degraded makes that true.
            performance = {"connected": True, "error": "temporarily unavailable"}
        if performance.get("connected"):
            payload[field] = performance

    user_message = json.dumps(payload, ensure_ascii=False)

    try:
        result = timed_step(
            AGENT_NAME, "llm_call",
            lambda: safe_claude_json_call(SYSTEM, user_message, max_tokens=800,
                                          client_id=client_id, cost_category="claude_support"),
        )
    except Exception as e:
        # ANY failure — unparseable JSON, but also the API timeouts/overloads
        # that the old `except ClaudeJSONError` let through as a bare 500
        return _technical_failure(client_id, "general", message, "llm_call", e)

    _alert_degraded(client_id, "general", _degraded_reads(payload))
    reply = result.get("reply", "")
    needs_human_followup = bool(result.get("needs_human_followup", False))
    out_of_scope = bool(result.get("out_of_scope", False))
    if out_of_scope:
        needs_human_followup = True
        if not reply.strip():
            reply = chat_fallback.out_of_scope_reply("general", _client_language(client_id, message))
        # A handoff, not a purchase or a research question — nothing else runs
        _handoff_alert(client_id, "general", message, out_of_scope=True)
        return {"reply": reply, "needs_human_followup": True}

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
        except Exception as e:
            # The model's reply here is a HOLDING line ("מכינים לך הצעה") —
            # showing it after the build failed would promise a proposal that
            # is never coming. The technical handoff says what is true.
            return _technical_failure(client_id, "general",
                                      f"{message} [upgrade request: {upgrade_request}]",
                                      "upgrade_proposal_build", e)
        # _build_upgrade_proposal raised its own team alert
        return {"reply": reply, "needs_human_followup": False}

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
            # Stage 1's reply is a holding line ("שנייה, בודקים את זה לעומק")
            # — after a failed search it would promise a check that never
            # happens. Hand the question to a human instead.
            return _technical_failure(client_id, "general", message, "web_search", e)

    if needs_human_followup:
        _handoff_alert(client_id, "general", message, out_of_scope=False)

    return {"reply": reply, "needs_human_followup": needs_human_followup}


# ─── Specialist personas (READ-ONLY by construction — see module docstring) ──
# Each reads-function below may ONLY call read/informational getters. The
# persona path has no access to pause/resume/create/publish/budget functions
# anywhere — that's the enforced boundary, not the prompt text.

def _google_reads(client_id: int) -> dict:
    from agents.google_ads_agent import get_campaign_performance
    return {"google_ads_performance": get_campaign_performance(client_id)}


def _meta_reads(client_id: int) -> dict:
    from agents.meta_ads_agent import get_campaign_performance
    from agents.meta_content_agent import get_engagement_summary
    data = {"meta_ads_performance": get_campaign_performance(client_id)}
    try:
        engagement = get_engagement_summary(client_id)
        if engagement.get("connected"):
            data["meta_organic_engagement"] = engagement
    except Exception as e:
        log_step(AGENT_NAME, "persona_reads", f"client {client_id}: meta engagement failed: {e}")
    return data


def _website_reads(client_id: int) -> dict:
    # Deliberately NOT exposing seo_agent.get_pending_strategy here — that's
    # Johnny's ADMIN approval surface (strategy plans await HIS yes/no, not
    # the client's); surfacing it would tell clients to approve something
    # they can't see. Published/draft articles already show via the site
    # overview and recent_activity.
    from agents.website_agent import get_site_overview
    from agents.seo_agent import get_connected_tool
    data = {"website_overview": get_site_overview(client_id)}
    try:
        tool = get_connected_tool(client_id)
        if tool:
            data["seo_tool_connected"] = tool
    except Exception as e:
        log_step(AGENT_NAME, "persona_reads", f"client {client_id}: seo reads failed: {e}")
    # Landing pages belong to this specialist's domain (the client's web
    # presence), and dashboard_payload is a pure READ — it lists pages, URLs,
    # lead counts and the domain state, and can create/publish/price nothing.
    # That keeps the persona path's read-only-by-construction invariant intact.
    try:
        from agents.landing_page_agent import dashboard_payload
        data["landing_pages"] = dashboard_payload(client_id)
    except Exception as e:
        log_step(AGENT_NAME, "persona_reads", f"client {client_id}: landing reads failed: {e}")
    # The external CRM connection belongs to this specialist: it is where the
    # leads their forms capture end up. get_status is a pure READ (a stored row
    # plus the supported-vendor registry) — it verifies nothing and pushes
    # nothing, so the persona path stays read-only by construction.
    try:
        from agents.crm_agent import get_status
        data["external_crm"] = get_status(client_id)
    except Exception as e:
        log_step(AGENT_NAME, "persona_reads", f"client {client_id}: crm reads failed: {e}")
    # The existing-site assessment, so follow-up questions about migrate-vs-
    # rebuild are answered from what we actually detected. A stored activity
    # row — reading it re-probes nothing.
    try:
        from agents.website_agent import latest_assessment
        assessment = latest_assessment(client_id)
        if assessment:
            data["existing_site_assessment"] = assessment
    except Exception as e:
        log_step(AGENT_NAME, "persona_reads", f"client {client_id}: assessment read failed: {e}")
    return data


def _media_reads(client_id: int) -> dict:
    # Deliberately no TikTok live fetch here: its engagement getter refreshes
    # (rotates+stores) OAuth tokens as a side effect — credential maintenance,
    # not a pure read — and it's slow. Recent media/publish items already
    # appear in the shared recent_activity feed the persona receives.
    from agents.media_agent import get_monthly_usage
    data = {"media_usage_this_month": get_monthly_usage(client_id)}
    # Camera hesitation is a MEDIA conversation, so the facts it needs live
    # here: does this client already have an avatar, and what would one cost.
    # Both are pure reads — the persona still cannot create or buy anything.
    try:
        from agents.avatar_agent import list_ready_avatars
        from agents.onboarding_agent import PRICING
        avatar_pricing = PRICING.get("avatar") or {}
        data["avatar_option"] = {
            "ready_avatars": len(list_ready_avatars(client_id) or []),
            "setup_ils": avatar_pricing.get("setup_first_avatar"),
            "monthly_tiers": avatar_pricing.get("monthly_tiers", [])[:1],
        }
    except Exception as e:
        log_step(AGENT_NAME, "persona_reads", f"client {client_id}: avatar reads failed: {e}")
    return data


PERSONAS = {
    "google": {
        "name": "יואב",
        "domain": "Google Ads — the client's paid search campaigns on Google",
        "voice": ("Sharp, numbers-first, loves explaining what a metric actually means in "
                  "plain words. Gets genuinely excited by a good click-through rate."),
        "reads": _google_reads,
        "data_notes": ("'google_ads_performance' is REAL live data from the client's own "
                       "Google Ads account (last 30 days, cost in ILS). If it has an 'error' "
                       "field: temporary read issue, the team is on it. If 'connected' is "
                       "false or the field is missing: the account isn't connected — warmly "
                       "point to the 'חבר עכשיו' button in the dashboard."),
    },
    "meta": {
        "name": "מאיה",
        "domain": "Meta — the client's Facebook + Instagram, both paid campaigns and organic page activity",
        "voice": ("Warm, social, thinks in audiences and moments. Connects numbers back to "
                  "how real people scroll, stop, and react."),
        "reads": _meta_reads,
        "data_notes": ("'meta_ads_performance' is REAL live paid-campaign data (last 30 days, "
                       "ILS, Facebook+Instagram combined); 'meta_organic_engagement' is REAL "
                       "organic post/engagement data when present. 'error' field: temporary "
                       "read issue. Fields missing/not connected: point warmly to 'חבר עכשיו' "
                       "in the dashboard."),
    },
    "website": {
        "name": "אורי",
        "domain": "the client's website (WordPress) and organic SEO — content, articles, rankings groundwork",
        "voice": ("Methodical builder, patient, explains that organic growth compounds — "
                  "honest that SEO is a months-not-days game, never promises rankings."),
        "reads": _website_reads,
        "data_notes": ("'website_overview' is REAL live data from the client's connected "
                       "WordPress site (recent posts/pages and statuses, SEO plugin state). "
                       "'seo_tool_connected' names their connected SEO tool when present. "
                       "New articles are written as DRAFTS for human review before "
                       "publishing — that's deliberate quality control, frame it that way. "
                       "Missing/not connected: point to the 'האתר שלך' card. "
                       "'landing_pages' is their LANDING PAGES — a separate thing from the "
                       "WordPress site: light, single-offer pages for capturing enquiries. "
                       "'included' of them come free with every package. 'domain.mode' is "
                       "'shared' (still on our temporary address), 'pending' (waiting for "
                       "their DNS record — if they say they added it, tell them to press "
                       "'הוספתי — בדקו עכשיו' in the דפי נחיתה section) or 'active' (running "
                       "on their own domain). If they want a page BEYOND the included "
                       "number, never quote a price — say you'll pass it to the team. "
                       "'external_crm' is their OPTIONAL connection to a CRM they already "
                       "own — leads we capture are copied there automatically, on top of "
                       "(never instead of) the leads list in their dashboard. 'vendors' "
                       "lists what we support today; only those. If they ask about a CRM "
                       "that isn't in that list — Zoho, Salesforce, Monday, anything — say "
                       "warmly that it isn't supported yet and you'll pass the request to "
                       "the team; NEVER improvise setup steps for it. When 'connected' is "
                       "false and they want to connect, walk them through the card in the "
                       "לידים section using the vendor's own 'help_he' text, and be clear "
                       "it is optional — nothing about their leads changes if they skip it. "
                       "If 'recent' shows failures, tell them plainly that those leads ARE "
                       "safe in uallak and only the copy to their CRM failed, and that we "
                       "retry automatically. "
                       "'existing_site_assessment' is what we DETECTED about a site they "
                       "already had, and it decides which of two different things we offer. "
                       "offered_path 'migrate' (their site IS WordPress): a real transfer — "
                       "content, design, images and pages come across as they are. It is "
                       "human-assisted and takes 1-3 business days, never instant, and their "
                       "existing site keeps running untouched until they approve the copy. "
                       "offered_path 'rebuild' (not WordPress, or we could not see the site): "
                       "there is NO way to transfer it — say so plainly and early. We build a "
                       "NEW site informed by the old one: same business, same services, new "
                       "design. Never call that a migration, never imply the design will look "
                       "the same, and never promise content moves automatically — we carry "
                       "across what matters, by hand. If 'reachable' is false we simply could "
                       "not load their site; ask them to double-check the address rather than "
                       "concluding anything about what it is built with."),
    },
    "media": {
        "name": "ליאור",
        "domain": "visual content and media — the images/videos uallak generates, filming kits, and the weekly content plan",
        "voice": ("Creative spark, visual thinker, encouraging about self-filmed content — "
                  "believes every business has a story worth showing, not just telling."),
        "reads": _media_reads,
        "data_notes": ("'media_usage_this_month' is REAL data on media generated this month "
                       "(images/videos/credits). Generated media lands in the client's Google "
                       "Drive folder for their review — nothing is ever auto-published. The "
                       "weekly content plan arrives via the dashboard's 'ממתין לאישור שלך' "
                       "area every Saturday night; approving it is what starts production. "
                       "CAMERA HESITATION — if they say they're uncomfortable on camera, "
                       "don't rush past it and don't sell immediately. FIRST: encourage them "
                       "genuinely — most owners feel this, the first take is always the worst, "
                       "it gets easier, and we send a filming kit with a script, a shot list "
                       "and coaching so they are never improvising. THEN, as a real second "
                       "option in the same breath (not a consolation prize): we can build an "
                       "AI avatar that looks and sounds like them and films instead of them — "
                       "a PAID add-on, setup plus a monthly tier, and it needs their recorded "
                       "consent. Quote 'avatar_option' figures ONLY if present and say they're "
                       "a starting point, not a final quote. If ready_avatars > 0 they already "
                       "have one — remind them rather than pitching it again. Let them choose; "
                       "never imply filming themselves is the lesser option."),
    },
}

PERSONA_SYSTEM_TEMPLATE = """You are {name}, uallak's {domain} specialist, chatting with an
existing, already-paying client inside their private dashboard. You are one member of the
uallak team wrapped around this client — never say you are an AI or a bot.

YOUR VOICE: {voice} Energetic and positive, but grounded: every number you cite must come
from the data given to you. Never invent stats, results, features, or activity. Same honesty
bar as the whole team — enthusiasm never outruns the data.

YOUR DATA: {data_notes}

Also provided: the client's package/proposal details, recent account activity, and
"conversation_history" (current thread, oldest first — use it to resolve references and never
re-ask what's already been said).

YOU CANNOT TAKE ACTIONS — THIS IS A FACT, NOT A POLICY. You have no ability to pause/resume/
create campaigns, change budgets, publish or edit content, or change anything on any account.
If the client asks you to change something (any phrasing, including urgent or insistent):
warmly explain that changes go through the uallak team and the dashboard's approval flow —
you'll flag it for the team right away (set needs_human_followup to true) — and continue
being helpful about what the DATA shows. Never say you did, will do, or scheduled a change.

STAY IN YOUR LANE, WARMLY: for questions clearly outside your domain but still about uallak
(billing, upgrades, another platform's campaigns, general account questions), give a one-line
friendly redirect to the main uallak chat (the default assistant in this same panel) — don't
attempt a full answer outside your domain. That is a redirect, not a handoff: it is NOT
"out_of_scope" (below), which is for things nobody at uallak's chat should answer.

Keep replies short: 2-4 sentences, in the client's language (see CLIENT LANGUAGE; Hebrew
default).

Return JSON only:
{{"reply": "client-language text", "needs_human_followup": true/false, "out_of_scope": true/false}}""" + HANDOFF_RULE + LANGUAGE_RULE


def answer_persona_question(client_id: int, persona_id: str, message: str) -> dict:
    """The specialist-persona chat path. READ-ONLY BY CONSTRUCTION — see the
    module docstring. Contract: only 'reply' and 'needs_human_followup' are
    ever read from the model's output; there is no upgrade path, no web
    search, and no action dispatch of any kind here."""
    persona = PERSONAS.get(persona_id)
    if not persona:
        return {"reply": "", "needs_human_followup": False, "error": "unknown persona"}
    # Same never-raise / never-empty contract as answer_support_question, with
    # the failure line in THIS specialist's lane
    try:
        result = _answer_persona_question(client_id, persona_id, persona, message)
    except Exception as e:
        return _technical_failure(client_id, persona_id, message, "persona_turn", e)
    if not (result.get("reply") or "").strip():
        return _technical_failure(client_id, persona_id, message, "persona_turn",
                                  RuntimeError("model returned an empty reply"))
    return result


def _answer_persona_question(client_id: int, persona_id: str, persona: dict, message: str) -> dict:
    log_step(AGENT_NAME, "answer_persona_question",
             f"client_id={client_id} persona={persona_id}")
    client = get_client(client_id)
    lead = _latest_lead(client.get("email", ""))
    payload = {
        "client": {"name": client.get("name"), "package": client.get("package"),
                    "status": client.get("status")},
        "proposal": lead.get("proposal") or {},
        "recent_activity": get_activity(client_id, limit=15),
        "conversation_history": _current_thread(client_id, client.get("chat_started_at"),
                                                channel=persona_channel(persona_id)),
        "client_message": message,
    }
    try:
        payload.update(persona["reads"](client_id))
    except Exception as e:
        # A broken platform read must never 500 the chat. Marked rather than
        # dropped (same reasoning as the concierge's platform loop): the model
        # says it's having trouble pulling the data, and _alert_degraded tells
        # the team.
        log_step(AGENT_NAME, "persona_reads",
                 f"client {client_id}: {persona_id} reads failed (degrading): {e}")
        payload[f"{persona_id}_data"] = {"error": "temporarily unavailable"}

    system = PERSONA_SYSTEM_TEMPLATE.format(
        name=persona["name"], domain=persona["domain"],
        voice=persona["voice"], data_notes=persona["data_notes"])
    try:
        result = timed_step(
            AGENT_NAME, "persona_llm_call",
            lambda: safe_claude_json_call(system, json.dumps(payload, ensure_ascii=False),
                                          max_tokens=700, client_id=client_id,
                                          cost_category="claude_support"))
    except Exception as e:
        return _technical_failure(client_id, persona_id, message, "persona_llm_call", e)

    _alert_degraded(client_id, persona_id, _degraded_reads(payload))
    reply = result.get("reply", "")
    out_of_scope = bool(result.get("out_of_scope", False))
    needs_human_followup = bool(result.get("needs_human_followup", False)) or out_of_scope
    if out_of_scope and not reply.strip():
        reply = chat_fallback.out_of_scope_reply(persona_id, _client_language(client_id, message))
    if needs_human_followup:
        _handoff_alert(client_id, persona_id, message, out_of_scope)
    # ONLY reply + needs_human_followup leave this function (out_of_scope is
    # read above for routing, never passed on); anything else is dropped unread
    return {"reply": reply, "needs_human_followup": needs_human_followup}
