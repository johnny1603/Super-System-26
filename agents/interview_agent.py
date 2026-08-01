"""First-login interview — a real conversation, not a slideshow.

## What it replaces

The dashboard opened on a static 5-step `welcome-modal` tour: correct
information, zero listening. It told the client what uallak does and learned
nothing back, while the four agents that could most use business specifics
(SEO, Media, Ads, Website) were left inferring everything from a sales chat
that happened before the client had ever seen the product.

This is that same first-login moment, run as a conversation. The tour markup
stays for the "?" replay button — a client who wants the guided tour again can
still have it; it just isn't the first thing that happens.

## The two jobs it holds at once

1. **Explain**, answering whatever the client actually asks — free-form. A
   client who asks "what is a pixel" mid-interview gets a real answer, then
   the thread resumes. It is NOT a form wearing a chat costume.
2. **Gather what only the client knows**: competitor names and links above
   all, plus whatever else the domain agents can use. The sales chat already
   asked for competitors ("a name or two is gold"), but the client had no
   reason to be thorough while being sold to; asking again, from inside the
   product, is a genuinely different conversation.

## Where the answers go — the ONE rule that matters

Captured facts land in `client_activity` and are read back by
`core/competitor_research.py`, which is already the single place the media,
website and ads agents get market context from (and which already folds in
the paid SEO tool's competitor domains). **No new table, no new silo.** If a
future agent needs these facts, it should call competitor_research like the
others do rather than reading these rows directly.

## Persona routing

Domain questions are answered in the voice of the specialist who owns them —
reusing `support_agent.PERSONAS` rather than defining a second cast. The
interview names which specialist is speaking on each turn, so the client
learns the team while being interviewed by it. The specialists themselves stay
READ-ONLY (support_agent's invariant): this agent writes the captured facts,
personas never do.

## The 90-day urgency (Part 4's dependency, built here on purpose)

The 90-day goal clock starts only when every integration is connected, so the
single most valuable thing this conversation can do is get them all connected
on day one. The prompt is told to raise that — honestly, as a reason to act
today, never as a fake deadline. The cycle logic itself lands in the next
commit; this is the half that has to live in the first conversation.
"""
import json
import os
from datetime import datetime, timezone

from core.agent_base import agent_alert, log_step, timed_step
from core.claude_json import ClaudeJSONError, safe_claude_json_call

AGENT_NAME = "interview_agent"

# The interview is done when the client says so or when we have enough. Kept
# short on purpose: this is someone's first minute in the product, and an
# interview that outstays its welcome costs more than the last two answers are
# worth.
MAX_TURNS = 12

CAPTURE_ACTION = "client_facts_captured"
INTERVIEW_DONE_ACTION = "first_login_interview_completed"

_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        from supabase import create_client
        _db_instance = create_client(os.environ["SUPABASE_URL"],
                                     os.environ["SUPABASE_SERVICE_KEY"])
    return _db_instance


def _log_activity(client_id: int, action_type: str, details: dict, result: dict = None):
    from agents.client_agent import log_activity
    log_activity(client_id, AGENT_NAME, action_type, details, result or {})


# ─── State ────────────────────────────────────────────────────────────────────

def is_completed(client_id: int) -> bool:
    rows = (_db().table("client_activity").select("id")
            .eq("client_id", client_id).eq("agent_name", AGENT_NAME)
            .eq("action_type", INTERVIEW_DONE_ACTION).limit(1).execute().data or [])
    return bool(rows)


def captured_facts(client_id: int) -> dict:
    """Everything gathered so far, newest-wins per field. Read by
    core/competitor_research.py — see the module docstring."""
    rows = (_db().table("client_activity").select("details,created_at")
            .eq("client_id", client_id).eq("agent_name", AGENT_NAME)
            .eq("action_type", CAPTURE_ACTION)
            .order("created_at", desc=True).limit(20).execute().data or [])
    facts = {"competitors": [], "notes": {}}
    seen = set()
    for row in rows:  # newest first
        details = row.get("details") or {}
        for competitor in (details.get("competitors") or []):
            key = (competitor.get("name", "") or competitor.get("url", "")).strip().lower()
            if key and key not in seen:
                seen.add(key)
                facts["competitors"].append(competitor)
        for key, value in (details.get("notes") or {}).items():
            facts["notes"].setdefault(key, value)
    return facts


def _transcript(client_id: int) -> list:
    """The interview's own turns. Uses the SAME communications channel the
    concierge chat uses ('dashboard_chat'), because this conversation IS the
    start of that relationship — a separate channel would make the client's
    first exchange vanish from their chat history the moment it ended."""
    from agents.client_agent import get_communications
    # channel filter is done by the query, and the text column is `content`
    # (client_communications) — not `message`.
    rows = get_communications(client_id, limit=40, channel="dashboard_chat") or []
    turns = [{"role": "assistant" if row.get("direction") == "outbound" else "user",
              "text": (row.get("content") or "")[:1500]}
             for row in reversed(rows)]
    return turns[-(MAX_TURNS * 2):]


# ─── The conversation ─────────────────────────────────────────────────────────

INTERVIEW_SYSTEM = """You are the uallak team greeting a NEW client inside their dashboard,
minutes after they first logged in. This is a real conversation, not a form and not a
slideshow. You have two jobs at once, and the second never blocks the first.

JOB 1 — BE USEFUL. Answer whatever they ask about the platform, honestly and in plain
words. If they ask something mid-interview, answer it properly first, then continue. Never
say "we'll get to that" to a direct question.

JOB 2 — LEARN WHAT ONLY THEY KNOW. Across the conversation, gather:
- COMPETITORS above all: names AND website links. This is the single most valuable thing
  here — our research agents work far better with real names than with a guess at the
  niche. Ask for 2-3, and ask for links, not just names.
- Anything else a specialist could use: who their best customers actually are, what they
  sell most, seasonality, what they've tried before that flopped, whether they're willing
  to appear on camera.
Ask ONE thing at a time. Never present a numbered list of questions.

SPEAK AS THE RIGHT SPECIALIST. The team: {personas}. When the turn is about a specific
domain, answer AS that specialist and say who you are ("אני אורי, אחראי על האתר וה-SEO
שלכם"). For general/platform talk, you are the concierge and no name is needed.

THE 90-DAY CLOCK — raise this early, once, and honestly: their 90-day goal cycle starts
only when ALL their integrations are connected, so connecting everything TODAY means the
clock starts today instead of a week from now. Currently connected: {connected}. Missing:
{missing}. Frame it as a reason to finish today — never as a deadline, a penalty, or a
threat. If nothing is missing, congratulate them on being fully set up and skip it.

TONE: warm, direct, human. You are a team wrapped around this business, never an AI or a
bot. Never invent facts about their business, their results, or what uallak has done for
them so far — you have just met.

LANGUAGE: {language_rule}

HARD LIMITS: your reply is max 4 sentences. One question per reply, at most. No bullet
lists unless they asked for one.

Return JSON only:
{{"reply": "your message to the client",
  "speaking_as": "general|google|meta|website|media",
  "captured": {{"competitors": [{{"name": "", "url": ""}}],
                "notes": {{"key": "short value"}}}},
  "interview_complete": false}}

`captured` holds ONLY facts stated in the client's latest message — leave it empty
otherwise, and NEVER copy something they did not say. Set interview_complete true when
they ask to stop, or once you have competitors plus a couple of useful notes."""


def _persona_summary() -> str:
    from agents.support_agent import PERSONAS
    return "; ".join(f"{p['name']} ({key}) — {p['domain']}" for key, p in PERSONAS.items())


def start_or_continue(client_id: int, message: str = "", language: str = "he") -> dict:
    """One interview turn. `message` empty = the opening turn (nothing said yet).

    Returns {"reply", "speaking_as", "complete", "captured_count"}. Never
    raises for an LLM failure: a broken first minute must not be a dead end —
    the client gets a warm fallback and can talk to the normal chat instead.
    """
    from agents.client_agent import get_client, log_communication
    from agents.onboarding_agent import LANGUAGE_RULE
    from core import client_journey

    client = get_client(client_id) or {}
    status = client_journey.connection_status(client_id)

    if message:
        # The client's own turn is recorded BEFORE the LLM call, so a failure
        # below never loses what they typed.
        log_communication(client_id, "inbound", "dashboard_chat", message)

    system = INTERVIEW_SYSTEM.format(
        personas=_persona_summary(),
        connected=", ".join(status["connected"]) or "(none yet)",
        missing=", ".join(status["missing"]) or "(none — everything is connected)",
        language_rule=LANGUAGE_RULE,
    )
    payload = {
        "client_name": client.get("name", ""),
        "business_name": client.get("business_name", ""),
        "package": client.get("package", ""),
        "ui_language": language,
        "conversation_so_far": _transcript(client_id),
        "client_latest_message": message,
        "already_captured": captured_facts(client_id),
    }

    log_step(AGENT_NAME, "interview_turn",
             f"client {client_id}: {'opening' if not message else 'turn'}")
    try:
        result = timed_step(
            AGENT_NAME, "interview_llm",
            lambda: safe_claude_json_call(system, json.dumps(payload, ensure_ascii=False),
                                          max_tokens=700, client_id=client_id,
                                          cost_category="claude_interview"))
    except ClaudeJSONError as e:
        agent_alert(AGENT_NAME, [f"client {client_id}: first-login interview turn failed: {e}"])
        # A first-login dead end is worse than an imperfect reply.
        fallback = ("שמחים שאתם כאן! 🙂 אני כאן לכל שאלה — ובינתיים, "
                    "אם יש לכם שמות של מתחרים שכדאי שנכיר, כתבו לי אותם כאן.")
        log_communication(client_id, "outbound", "dashboard_chat", fallback)
        return {"success": True, "reply": fallback, "speaking_as": "general",
                "complete": False, "captured_count": 0, "degraded": True}

    reply = (result.get("reply") or "").strip()
    if reply:
        log_communication(client_id, "outbound", "dashboard_chat", reply)

    captured_count = _store_captured(client_id, result.get("captured") or {})

    complete = bool(result.get("interview_complete")) or len(_transcript(client_id)) >= MAX_TURNS * 2
    if complete and not is_completed(client_id):
        facts = captured_facts(client_id)
        _log_activity(client_id, INTERVIEW_DONE_ACTION,
                      {"competitors": len(facts["competitors"]),
                       "notes": sorted(facts["notes"].keys())})
        log_step(AGENT_NAME, "interview_complete",
                 f"client {client_id}: {len(facts['competitors'])} competitors captured")

    return {"success": True, "reply": reply,
            "speaking_as": result.get("speaking_as", "general"),
            "complete": complete, "captured_count": captured_count}


def _store_captured(client_id: int, captured: dict) -> int:
    """Persist this turn's facts. Stored as an activity row so
    competitor_research reads them through the path every agent already uses
    (see the module docstring) — deliberately NOT a new table."""
    competitors = []
    for item in (captured.get("competitors") or [])[:10]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:120]
        url = str(item.get("url") or "").strip()[:300]
        if name or url:
            competitors.append({"name": name, "url": url})
    raw_notes = captured.get("notes")
    notes = ({str(k)[:60]: str(v)[:400] for k, v in raw_notes.items() if v}
             if isinstance(raw_notes, dict) else {})

    if not competitors and not notes:
        return 0
    _log_activity(client_id, CAPTURE_ACTION,
                  {"competitors": competitors, "notes": notes,
                   "at": datetime.now(timezone.utc).isoformat()})
    return len(competitors) + len(notes)
