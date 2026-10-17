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

THE ONE EXCEPTION (2026-10-17): the WEBSITE persona may LABEL a request for
a new article ("article_request": topic/tone/link). The model writes nothing
and decides nothing; code (_article_draft) calls seo_agent.write_article,
which saves a WordPress DRAFT only — weekly cap, topic dedup and the quality
gate all enforced there — and the confirmation the client sees is fixed
text with the draft's wp-admin link. Nothing is ever published or edited
from the chat; the client presses Publish in WordPress. Any other action
label stays unread.
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


def _latest_lead(email: str) -> dict:
    """The client's original sales-chat record (answers + proposal), matched by
    email. The answers feed upgrade proposals; the proposal feeds everyday
    questions. NEVER an in-chat upgrade quote — those are lead rows too, and
    reading one as "what they bought" is the bug
    core/lead_tracking.client_sales_lead exists to prevent."""
    if not email:
        return {}
    from core.lead_tracking import client_sales_lead
    row, _how = client_sales_lead(email=email, columns="answers,proposal")
    return row


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

ANSWER BY DEFAULT. Almost every message is one you simply answer, directly and warmly, with no
handoff: greetings, "can I talk to you about my business?" (yes — invite them to share),
questions about their business, marketing ideas, what uallak does, how the service or dashboard
works, their package, what happens next. Being unsure of a detail is not a handoff either — say
what you do know. The case below is a RARE exception; when in doubt, it is NOT one.

THE EXCEPTION — never a dead end, always in your own voice:
- OUT OF SCOPE — only for a question that needs a licensed professional or a decision only the
  team can make: legal, tax/accounting, medical, insurance, contract terms, refunds, exceptions
  to the package, guarantees of results. Don't attempt it and never bluff: say in one short line,
  the way you normally talk, that it isn't something you can help with directly and you're
  passing it to the team, then keep going with what you CAN help with. Set "out_of_scope" AND
  "needs_human_followup" to true — that is what actually sends it to the team.
- MOVE ON: name a limitation once. Don't apologize repeatedly, and don't bring up an earlier
  handoff from conversation_history unless the client does.

DATA TOPIC — label every message with "data_topic", the live account data it asks about:
"google_ads" (their Google campaigns' numbers or results), "meta_ads" (their Facebook/Instagram
campaigns' numbers or results), "website" (what is on or happening with their connected
website's posts and pages), or "" for everything else — greetings, ideas, advice, strategy,
how things work, their package, pricing, landing pages, leads. Label the QUESTION, not your
answer: set it the same way whether or not that data is in front of you. If a platform's field
is missing from the data entirely, you simply don't have those numbers in front of you — never
guess them."""

# Shared by the concierge and every persona (core/client_knowledge). Brace-free,
# like HANDOFF_RULE: concatenated into PERSONA_SYSTEM_TEMPLATE before .format().
KNOWLEDGE_RULE = """

CLIENT KNOWLEDGE: "client_knowledge" is what the team already knows about this client's
business — what they told us when they joined (the sales chat and the first-login
conversation), their real competitors, their 90-day plan and where they are in it, what they
have approved or turned down. Use it to be specific: name their actual competitors, tie advice
to their stated goal and customers, and never re-ask something it already answers. It is
background, not live metrics — numbers about their campaigns or site come only from the live
platform fields. Competitor research carries the date it was done ("researched_on"): treat it
as DATED BACKGROUND, not current fact — frame it as "when we last looked (that date)", never as
how things stand today. The 90-day goals and kpis are estimates, never promises. Anything not in
it you don't know yet — ask, or speak generally; never invent it. Asking about it is not live
data: data_topic stays "" for it. That includes WHICH ad account they are connected to:
"connected_accounts" states it (connected or not, and the account number exactly as their own
ad platform shows it, e.g. Google Ads 123-456-7890) — answer that from there, plainly."""

SYSTEM = """You are uallak's professional support assistant, chatting with an existing,
already-paying client inside their private dashboard. You are presented as "the uallak
team" - never say you are an AI or a bot.

Talk with them about their business, their marketing, their package, pricing and how the
system works. For FACTS about their account (numbers, features in their package, activity),
use ONLY the client data given to you in the user message (their package/proposal details,
client_knowledge and recent account activity) - never invent numbers, features, or activity
not present in it.
General conversation and marketing advice don't need data; answer those directly.

Platform data: if the user message includes a "google_ads_performance" or
"meta_ads_performance" field, that is REAL live data from the client's own ad account
(last 30 days; "cost" values are in the account's currency, ILS for our clients;
meta_ads_performance covers Facebook + Instagram combined) - use it to answer campaign
questions concretely, citing the actual numbers. If a platform's field says "connected": false
and the question needs that platform's data, that account isn't connected yet - warmly point
them to the "חבר עכשיו" button in the dashboard. TikTok has no live connection yet; if asked
about it, say so honestly and mention it's coming soon. Never invent campaign numbers.

Website data: a "website_overview" field is REAL live data from the client's connected
WordPress site (site name, recent posts/pages and their statuses, whether an SEO plugin is
installed) - use it for "what's happening with my site" questions, citing actual titles and
statuses only. "connected": false means the site isn't connected yet - point them to the
"האתר שלך" card in the dashboard.

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

Team follow-up (needs_human_followup) is for what you genuinely cannot resolve in chat: a
request to CHANGE something on their account, a complaint, a billing problem, or a specific
fact about their account that the data doesn't contain. Then say a team member will follow up
personally and set needs_human_followup to true. General conversation about their business,
marketing or how we work is NOT that — answer it yourself.

Keep replies short: 2-4 sentences max, in the client's language (the language of their message
and conversation_history — see CLIENT LANGUAGE; Hebrew default).

Return JSON only:
{"reply": "client-language text", "needs_human_followup": true/false, "out_of_scope": true/false,
 "data_topic": "", "web_search_query": "", "upgrade_request": ""}""" + HANDOFF_RULE + KNOWLEDGE_RULE \
    + LANGUAGE_RULE + _AUTOMATION_RULE

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


# ─── Failed reads: the model never sees them, code decides (2026-10-13) ───────
# History: failed reads were first kept in the payload as error blocks, then
# narrowed to an `unavailable_right_now` name list the model was trusted to
# ignore unless relevant. Both put failure state in front of the model on
# EVERY turn for a client with a persistently broken integration, and new
# phrasings kept finding new misfires ("having trouble pulling that up" on
# "can I talk to you about my business?"). Now: a failed read is simply left
# out of the payload; the model labels what each message asks about
# ("data_topic", same on healthy and broken accounts); and code shows the
# fixed "can't pull that up right now" line only when that label matches a
# read that actually failed in this same turn.
# See HANDOFF-chat-data-availability.md.

# Payload field -> data_topic. Only these three fields are live data a client
# can ask about by name; any other failed read is dropped without a topic.
TOPIC_FIELDS = {"google_ads_performance": "google_ads",
                "meta_ads_performance": "meta_ads",
                "website_overview": "website"}
# A persona's whole reads() raising takes down its own platform's data
PERSONA_TOPICS = {"google": "google_ads", "meta": "meta_ads", "website": "website"}


def _add_reads(payload: dict, reads: dict) -> set:
    """Merge read results into the payload, leaving out every one that came
    back with an error. Returns the data_topics that failed this turn."""
    failed = set()
    for field, value in reads.items():
        if isinstance(value, dict) and value.get("error"):
            if field in TOPIC_FIELDS:
                failed.add(TOPIC_FIELDS[field])
            continue
        payload[field] = value
    return failed


# The proposal's narrative fields now reach the chats through client_knowledge
# (from the same sales-lead row), so the raw proposal keeps only the rest —
# packages and prices, notes, disclaimers — instead of sending them twice.
_KNOWLEDGE_PROPOSAL_FIELDS = ("business_summary", "market_reality", "goals_90_days", "kpis")


def _proposal_for_chat(lead: dict) -> dict:
    return {k: v for k, v in (lead.get("proposal") or {}).items()
            if k not in _KNOWLEDGE_PROPOSAL_FIELDS}


def _client_knowledge(client_id: int, persona_id: str) -> dict:
    """Business grounding for this turn (core/client_knowledge). Read-only and
    never raises; a failure just means no knowledge block, never a mention of
    one."""
    try:
        from core import client_knowledge
        return client_knowledge.profile(client_id, persona_id)
    except Exception as e:
        log_step(AGENT_NAME, "client_knowledge",
                 f"client {client_id}: knowledge read failed (left out): {e}")
        return {}


def _recent_activity(client_id: int) -> list:
    """Recent activity for the chat payload, minus the hidden chat_fallback
    alert-dedup markers: they record past failures and handoffs, and putting
    them in front of the model is exactly the standing failure context this
    design removes (the client feed hides them too)."""
    return [a for a in get_activity(client_id, limit=15)
            if a.get("agent_name") != chat_fallback.AGENT_NAME]


def _data_unavailable(client_id: int, persona_id: str, message: str, result: dict,
                      failed: set):
    """Code's decision. When the model labelled the message with a topic whose
    read failed THIS turn, return the fixed in-voice reply (and alert once a
    day per client+topic, shared by every persona, carrying the question);
    otherwise None and the model's reply stands. Background breakage is
    alerted by the getters themselves (ads) and website_agent's health scan —
    this alert is only for "a client asked and we couldn't answer"."""
    topic = str(result.get("data_topic") or "").strip()
    if topic not in failed:
        return None
    chat_fallback.alert_handoff(
        chat_fallback.KIND_TECHNICAL, AGENT_NAME,
        f'client asked about {topic} data but it failed to load: "{message[:300]}"',
        client_id=client_id, persona=persona_id,
        issue_key=f"technical:read:{topic}")
    return {"reply": chat_fallback.data_unavailable_reply(
                persona_id, _client_language(client_id, message), topic),
            "needs_human_followup": True}


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
        "proposal": _proposal_for_chat(lead),
        "client_knowledge": _client_knowledge(client_id, "general"),
        "recent_activity": _recent_activity(client_id),
        "conversation_history": _current_thread(client_id, client.get("chat_started_at")),
        "client_message": message,
    }
    if not payload["client_knowledge"]:
        payload.pop("client_knowledge")  # absent, never an empty block to read into

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
    reads = {}
    for field, fetch in (("google_ads_performance", google_performance),
                         ("meta_ads_performance", meta_performance),
                         ("website_overview", website_overview)):
        # One broken integration must never take down the whole chat (the
        # created_at 500 of 2026-07-16). A throw counts as a failed read, same
        # as an error result: left out of the payload, decided on by code.
        try:
            reads[field] = fetch(client_id)
        except Exception as e:
            log_step(AGENT_NAME, "platform_context",
                     f"client {client_id}: {field} fetch failed (degrading): {e}")
            reads[field] = {"connected": True, "error": str(e)}
    # {"connected": false} stays in: "not connected" is a fact the model acts
    # on (the חבר עכשיו pointer), unlike a failure
    failed = _add_reads(payload, reads)

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

    # They asked about data whose read failed this turn: fixed line, nothing
    # else runs (an upgrade or search wouldn't be about their numbers anyway)
    unavailable = _data_unavailable(client_id, "general", message, result, failed)
    if unavailable:
        return unavailable

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
            "business_summary": ((lead.get("proposal") or {}).get("business_summary") or ""),
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
                       "Google Ads account (last 30 days, cost in ILS). If 'connected' is "
                       "false, the account isn't connected — warmly point to the 'חבר "
                       "עכשיו' button. If it's missing entirely, you don't have the numbers "
                       "in front of you — never guess them. WHICH account they're connected "
                       "to is in client_knowledge.connected_accounts; to switch, the Google "
                       "Ads card has a 'החלפת חשבון' button that lists every account their "
                       "Google login can reach — no reconnecting needed. If the account they "
                       "want ISN'T in that list, walk them through the three usual reasons, "
                       "simply: (1) it belongs to a different Google login — connect again "
                       "with that one, or add this login as a user in that Google Ads account "
                       "(Admin → Access and security); (2) it isn't active yet — finish its "
                       "setup and billing in Google Ads, or it was suspended/canceled there; "
                       "(3) they're looking at a manager (MCC) account — pick the ad account "
                       "under it. Manager accounts can't run campaigns themselves."),
    },
    "meta": {
        "name": "מאיה",
        "domain": "Meta — the client's Facebook + Instagram, both paid campaigns and organic page activity",
        "voice": ("Warm, social, thinks in audiences and moments. Connects numbers back to "
                  "how real people scroll, stop, and react."),
        "reads": _meta_reads,
        "data_notes": ("'meta_ads_performance' is REAL live paid-campaign data (last 30 days, "
                       "ILS, Facebook+Instagram combined); 'meta_organic_engagement' is REAL "
                       "organic post/engagement data when present. 'connected' false: "
                       "point warmly to 'חבר עכשיו' in the dashboard. Missing entirely: you "
                       "don't have those numbers in front of you — never guess them."),
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
                       "'connected' false: point to the 'האתר שלך' card. Missing entirely: "
                       "you don't have it in front of you — never guess. "
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

# ─── The website persona's one action: an article DRAFT (code-run) ───────────
# Substituted into PERSONA_SYSTEM_TEMPLATE as format VALUES, so braces in them
# are literal. Same capture-of-intent bar as the concierge's upgrade_request:
# explicit ask only, ONE clarifying question when unclear, a holding reply that
# code replaces, hard length caps applied in code.
ARTICLE_EXCEPTION = " — with ONE exception, ARTICLE DRAFTS, described just below"
ARTICLE_FIELD = ', "article_request": {"topic": "", "tone": "", "link": ""}'
ARTICLE_DRAFT_RULE = """

ARTICLE DRAFTS — THE ONE THING YOU CAN SET IN MOTION. When the client explicitly asks for a new
article / blog post to be written FOR THEIR OWN SITE (any phrasing: "תכתוב מאמר ותעלה לאתר",
"write a post about X for my blog"), fill "article_request". The system then writes the
article and saves it on their site as a DRAFT — never published — and tells them itself, with a
link to review it. They publish it themselves when they are happy with it.
- Fill it ONLY for an explicit request to write one, with a clear topic. NOT for "what should I
  write about?", brainstorming, feedback on existing posts, or questions about their site.
- Topic unclear? Ask ONE short question (what it should be about; tone and a link to include
  are optional) and leave "article_request" empty. Never ask more than one question for this.
- "topic": the subject, in the client's words, max 150 characters. "tone": only if they stated
  one. "link": only a URL the client gave to include in the article, copied exactly. Leave
  unknown fields "".
- website_overview "connected": false → leave it empty and point them to the 'האתר שלך' card.
- When you fill it, "reply" is one short holding line (e.g. "מעולה, ניגש לכתוב טיוטה") — the
  system replaces it with the real outcome. Never claim the article exists or is live.
- Publishing, editing or deleting existing content stays a team request, exactly as above."""

ARTICLE_TOPIC_MAX, ARTICLE_TONE_MAX, ARTICLE_LINK_MAX = 150, 80, 300

# Fixed client-facing outcomes (code decides which — the model never sees
# them). Gender-neutral first person, like chat_fallback's fixed lines.
_ARTICLE_DONE = {
    "he": 'הטיוטה מוכנה ✍️ "{title}" נשמרה באתר שלכם כטיוטה — שום דבר לא עלה לאוויר. אפשר לעבור עליה, לערוך ולפרסם כשנוח לכם:\n{url}',
    "en": 'Your draft is ready ✍️ "{title}" is saved on your site as a DRAFT — nothing has gone live. Review it, edit it and publish it whenever you like:\n{url}',
    "fr": 'Votre brouillon est prêt ✍️ « {title} » est enregistré sur votre site en BROUILLON — rien n\'est en ligne. Relisez-le, modifiez-le et publiez-le quand vous voulez :\n{url}',
    "ar": 'المسودة جاهزة ✍️ "{title}" محفوظة في موقعكم كمسودة — لم يُنشر شيء. يمكنكم مراجعتها وتعديلها ونشرها متى شئتم:\n{url}',
    "ru": 'Черновик готов ✍️ «{title}» сохранён на вашем сайте как ЧЕРНОВИК — ничего не опубликовано. Просмотрите, отредактируйте и опубликуйте, когда будет удобно:\n{url}',
}
_ARTICLE_NO_URL = {
    "he": 'אפשר למצוא אותה תחת "פוסטים" בניהול האתר.',
    "en": 'You\'ll find it under "Posts" in your site\'s admin.',
    "fr": 'Vous le trouverez dans « Articles » de l\'administration du site.',
    "ar": 'ستجدونها ضمن "المقالات" في لوحة إدارة الموقع.',
    "ru": 'Он находится в разделе «Записи» в админке сайта.',
}
_ARTICLE_WEEKLY_CAP = {
    "he": "אנחנו כותבים עד {cap} מאמרים חדשים בשבוע, בכוונה — תוכן בכמויות פוגע בדירוג בגוגל. המכסה של השבוע כבר נוצלה; כתבו לי שוב בשבוע הבא ואכין טיוטה על הנושא הזה.",
    "en": "We write up to {cap} new articles a week, on purpose — mass-produced content hurts Google rankings. This week's are already done; message me again next week and I'll draft this topic.",
    "fr": "Nous rédigeons jusqu'à {cap} nouveaux articles par semaine, volontairement — le contenu en masse nuit au classement Google. Ceux de cette semaine sont déjà faits ; écrivez-moi la semaine prochaine et je rédigerai ce sujet.",
    "ar": "نكتب حتى {cap} مقالات جديدة أسبوعيًا عن قصد — المحتوى بالجملة يضر بترتيب Google. مقالات هذا الأسبوع أُنجزت بالفعل؛ راسلوني الأسبوع القادم وسأكتب مسودة لهذا الموضوع.",
    "ru": "Мы пишем до {cap} новых статей в неделю — намеренно: массовый контент вредит позициям в Google. На этой неделе лимит уже исчерпан; напишите мне на следующей неделе, и я подготовлю черновик на эту тему.",
}
_ARTICLE_DUPLICATE = {
    "he": "כבר כתבנו לאחרונה מאמר על בדיוק הנושא הזה. רוצים זווית אחרת? ספרו לי במה להתמקד ואכין טיוטה חדשה.",
    "en": "We recently wrote an article on exactly this topic. Want a different angle? Tell me what to focus on and I'll draft a new one.",
    "fr": "Nous avons récemment écrit un article sur exactement ce sujet. Un autre angle ? Dites-moi sur quoi insister et je rédigerai un nouveau brouillon.",
    "ar": "كتبنا مؤخرًا مقالًا عن هذا الموضوع بالضبط. تريدون زاوية مختلفة؟ أخبروني على ماذا أركّز وسأكتب مسودة جديدة.",
    "ru": "Недавно мы уже писали статью ровно на эту тему. Хотите другой ракурс? Скажите, на чём сделать акцент, и я подготовлю новый черновик.",
}
_ARTICLE_NOT_IN_PACKAGE = {
    "he": "כתיבת מאמרים לאתר צריכה להיות חלק מהחבילה שלכם — העברתי את הבקשה לצוות, ויחזרו אליכם.",
    "en": "Writing articles for your site needs to be part of your package — I've passed your request to the team and they'll get back to you.",
    "fr": "La rédaction d'articles pour votre site doit faire partie de votre formule — j'ai transmis votre demande à l'équipe, qui reviendra vers vous.",
    "ar": "كتابة المقالات لموقعكم يجب أن تكون ضمن باقتكم — حوّلت طلبكم للفريق وسيعودون إليكم.",
    "ru": "Написание статей для сайта должно входить в ваш пакет — я передал запрос команде, с вами свяжутся.",
}
_ARTICLE_SIMILAR_NOTE = {
    "he": "שימו לב: כבר כתבנו לאחרונה מאמר על נושא דומה — \"{title}\"{link}",
    "en": "Note: we recently wrote an article on a similar topic — \"{title}\"{link}",
    "fr": "À noter : nous avons récemment écrit un article sur un sujet proche — « {title} »{link}",
    "ar": "ملاحظة: كتبنا مؤخرًا مقالًا عن موضوع مشابه — \"{title}\"{link}",
    "ru": "Обратите внимание: недавно мы уже писали статью на похожую тему — «{title}»{link}",
}
_ARTICLE_NOT_CONNECTED = {
    "he": 'כדי שאוכל לשמור טיוטה באתר, צריך קודם לחבר אותו — בכרטיס "האתר שלך" בדשבורד. אחרי החיבור כתבו לי שוב ואתחיל.',
    "en": 'To save a draft on your site, it needs to be connected first — use the "Your website" card in the dashboard. Once it\'s connected, message me again and I\'ll start.',
    "fr": 'Pour enregistrer un brouillon sur votre site, il faut d\'abord le connecter — via la carte « Votre site » du tableau de bord. Ensuite, écrivez-moi et je m\'y mets.',
    "ar": 'لحفظ مسودة في موقعكم يجب ربطه أولًا — من بطاقة "موقعك" في لوحة التحكم. بعد الربط راسلوني وسأبدأ.',
    "ru": 'Чтобы сохранить черновик на сайте, его нужно сначала подключить — карточка «Ваш сайт» в панели. После подключения напишите мне, и я начну.',
}


def _article_brief(raw) -> dict:
    """The model's article_request, capped and cleaned in CODE. {} unless
    there is a usable topic. A link survives only as a plain http(s) URL."""
    if not isinstance(raw, dict):
        return {}
    topic = " ".join(str(raw.get("topic") or "").split())[:ARTICLE_TOPIC_MAX]
    if len(topic) < 3:
        return {}
    tone = " ".join(str(raw.get("tone") or "").split())[:ARTICLE_TONE_MAX]
    link = str(raw.get("link") or "").strip()
    if not (link.startswith(("https://", "http://")) and len(link) <= ARTICLE_LINK_MAX
            and not any(c.isspace() or c in '<>"\'' for c in link)):
        link = ""
    return {"topic": topic, "tone": tone, "link": link}


def _article_draft(client_id: int, message: str, brief: dict) -> dict:
    """Code's half of the website persona's one action. DRAFT ONLY:
    seo_agent.write_article hard-codes status='draft' and enforces the weekly
    cap, topic dedup and quality gate. Policy refusals get their own fixed
    line; any other failure gets the calm technical line and ONE deduped
    alert (write_article's own alerts are switched off for this path)."""
    lang = _client_language(client_id, message)
    lang = lang if lang in _ARTICLE_DONE else "he"
    # Package check (paying clients; per-client switch in core/client_settings).
    # Fails closed: an unreadable package is not proof they bought it.
    from core import client_settings
    if client_settings.get(client_id)["article_package_check"]:
        try:
            from core.client_journey import required_connections
            entitled = any(s["key"] == "website" for s in required_connections(client_id)["steps"])
        except Exception as e:
            log_step(AGENT_NAME, "article_draft", f"client {client_id}: package read failed: {e}")
            entitled = False
        if not entitled:
            chat_fallback.alert_handoff(
                chat_fallback.KIND_FOLLOWUP, AGENT_NAME,
                f'asked for an article draft but the package does not cover website content '
                f'(or could not be read). Topic: "{brief["topic"]}"',
                client_id=client_id, persona="website",
                issue_key="followup:website:article_not_in_package")
            return {"reply": _ARTICLE_NOT_IN_PACKAGE[lang], "needs_human_followup": True}
    notes = ["Requested by the client in the dashboard chat."]
    if brief["tone"]:
        notes.append(f"Tone requested by the client: {brief['tone']}.")
    if brief["link"]:
        notes.append(f"Include a natural, relevant link to {brief['link']} in the body.")
    log_step(AGENT_NAME, "article_draft", f"client {client_id}: '{brief['topic']}'")
    try:
        from agents.seo_agent import write_article
        from core.cost_tracker import cost_action
        with cost_action("article_draft"):
            result = timed_step(AGENT_NAME, "article_draft",
                                lambda: write_article(client_id, brief["topic"],
                                                      notes=" ".join(notes), alert=False))
    except Exception as e:
        result = {"success": False, "errors": [f"{type(e).__name__}: {e}"]}

    if result.get("success"):
        from agents.website_agent import draft_edit_url
        url = draft_edit_url(client_id, result.get("post_id")) or result.get("link", "")
        reply = _ARTICLE_DONE[lang].format(title=result.get("title", ""), url=url or _ARTICLE_NO_URL[lang])
        similar = result.get("similar") or {}
        if similar.get("title"):
            reply += "\n\n" + _ARTICLE_SIMILAR_NOTE[lang].format(
                title=similar["title"], link=f"\n{similar['link']}" if similar.get("link") else "")
        log_activity(client_id, AGENT_NAME, "article_draft_from_chat",
                     {"topic": brief["topic"], "tone": brief["tone"], "link": brief["link"]},
                     {"post_id": result.get("post_id"), "status": "draft"})
        return {"reply": reply, "needs_human_followup": False}

    reason = result.get("reason", "")
    if reason == "weekly_cap":
        return {"reply": _ARTICLE_WEEKLY_CAP[lang].format(cap=result.get("cap", 2)),
                "needs_human_followup": False}
    if reason == "duplicate_topic":
        return {"reply": _ARTICLE_DUPLICATE[lang], "needs_human_followup": False}
    if reason == "not_connected":
        return {"reply": _ARTICLE_NOT_CONNECTED[lang], "needs_human_followup": False}

    errors = "; ".join(str(x) for x in (result.get("errors") or ["unknown"]))[:300]
    chat_fallback.alert_handoff(
        chat_fallback.KIND_TECHNICAL, AGENT_NAME,
        f'article draft from chat failed ({errors}). Topic: "{brief["topic"]}". '
        f'Client asked: "{message[:300]}"',
        client_id=client_id, persona="website",
        issue_key="technical:website:article_draft")
    return {"reply": chat_fallback.technical_reply("website", lang),
            "needs_human_followup": True}


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
create campaigns, change budgets, publish or edit content, or change anything on any
account{action_exception}. If the client asks you to change something (any phrasing, including
urgent or insistent): warmly explain that changes go through the uallak team and the
dashboard's approval flow — you'll flag it for the team right away (set needs_human_followup
to true) — and continue being helpful about what the DATA shows. Never say you did, will do,
or scheduled a change.{action_rule}

STAY IN YOUR LANE, WARMLY: for questions clearly outside your domain but still about uallak
(billing, upgrades, another platform's campaigns, general account questions), give a one-line
friendly redirect to the main uallak chat (the default assistant in this same panel) — don't
attempt a full answer outside your domain. That is a redirect, not a handoff: it is NOT
"out_of_scope" (below), which is for things nobody at uallak's chat should answer.

Keep replies short: 2-4 sentences, in the client's language (see CLIENT LANGUAGE; Hebrew
default).

Return JSON only:
{{"reply": "client-language text", "needs_human_followup": true/false, "out_of_scope": true/false,
 "data_topic": ""{extra_fields}}}""" + HANDOFF_RULE + KNOWLEDGE_RULE + LANGUAGE_RULE


def answer_persona_question(client_id: int, persona_id: str, message: str) -> dict:
    """The specialist-persona chat path. READ-ONLY BY CONSTRUCTION — see the
    module docstring. Contract: only 'reply', 'needs_human_followup', and the
    routing labels 'out_of_scope' / 'data_topic' are ever read from the
    model's output — plus, for the website persona only, 'article_request',
    which code turns into a DRAFT (_article_draft). No upgrade path, no web
    search, no other action dispatch."""
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
        "proposal": _proposal_for_chat(lead),
        "client_knowledge": _client_knowledge(client_id, persona_id),
        "recent_activity": _recent_activity(client_id),
        "conversation_history": _current_thread(client_id, client.get("chat_started_at"),
                                                channel=persona_channel(persona_id)),
        "client_message": message,
    }
    if not payload["client_knowledge"]:
        payload.pop("client_knowledge")
    try:
        failed = _add_reads(payload, persona["reads"](client_id))
    except Exception as e:
        # A broken platform read must never 500 the chat. Same as the
        # concierge: nothing about the failure reaches the model; code decides
        # on the fixed line if the client asks about this persona's data.
        log_step(AGENT_NAME, "persona_reads",
                 f"client {client_id}: {persona_id} reads failed (degrading): {e}")
        failed = {PERSONA_TOPICS[persona_id]} if persona_id in PERSONA_TOPICS else set()

    drafts = persona_id == "website"
    system = PERSONA_SYSTEM_TEMPLATE.format(
        name=persona["name"], domain=persona["domain"],
        voice=persona["voice"], data_notes=persona["data_notes"],
        action_exception=ARTICLE_EXCEPTION if drafts else "",
        action_rule=ARTICLE_DRAFT_RULE if drafts else "",
        extra_fields=ARTICLE_FIELD if drafts else "")
    try:
        result = timed_step(
            AGENT_NAME, "persona_llm_call",
            lambda: safe_claude_json_call(system, json.dumps(payload, ensure_ascii=False),
                                          max_tokens=700, client_id=client_id,
                                          cost_category="claude_support"))
    except Exception as e:
        return _technical_failure(client_id, persona_id, message, "persona_llm_call", e)

    reply = result.get("reply", "")
    out_of_scope = bool(result.get("out_of_scope", False))
    if not out_of_scope:
        unavailable = _data_unavailable(client_id, persona_id, message, result, failed)
        if unavailable:
            return unavailable
        # The one action label (website persona only — see module docstring).
        # The model's reply is a holding line; code writes what the client sees.
        if drafts:
            brief = _article_brief(result.get("article_request"))
            if brief:
                return _article_draft(client_id, message, brief)
    needs_human_followup = bool(result.get("needs_human_followup", False)) or out_of_scope
    if out_of_scope and not reply.strip():
        reply = chat_fallback.out_of_scope_reply(persona_id, _client_language(client_id, message))
    if needs_human_followup:
        _handoff_alert(client_id, persona_id, message, out_of_scope)
    # ONLY reply + needs_human_followup leave this function (out_of_scope and
    # data_topic are read above for routing, never passed on); anything else
    # is dropped unread
    return {"reply": reply, "needs_human_followup": needs_human_followup}
