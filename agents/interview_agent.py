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
   **What it knows about the product comes from `core/feature_catalog.py`**,
   filtered to the client's own package — it is not written into this prompt.
   For a long time the prompt said "answer whatever they ask about the
   platform" while supplying no facts about the platform at all, so the model
   answered from generic knowledge. A new capability is one entry in that
   catalogue; do not describe features here.
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

## Two phases, and the gate between them (revised 2026-08-02)

1. `connection_nudge()` — the FIRST-login message, sent while integrations are
   still missing. Names the ONE real next step and says honestly why finishing
   today matters: the 90-day plan and this interview both start only once
   everything is connected. Deduped to once a day.
2. `start_or_continue()` — the DEEP interview, gated by `readiness()` on every
   required connection for the client's PURCHASED package being live
   (`core/client_journey.connection_status`).

The gate is what makes the urgency honest rather than a sales line: the
conversation genuinely does wait. It also makes the interview better — it can
ask about platforms it can actually see. It fails CLOSED on a package it
cannot resolve, and it blocks only the OPENING turn, so an interview already
under way is never dead-ended mid-sentence by a disconnect.
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
NUDGE_ACTION = "connection_nudge_sent"

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

WHAT THIS CLIENT ACTUALLY HAS — the complete list, already filtered to THEIR package.
This is the only thing you know about the product; never describe a capability that is not
on it, and never promise one that is not:
{feature_catalog}
The note in brackets after each item is how the client GETS it, and it is the part people
get wrong: "אפשר להפעיל לבד מהדשבורד" means point them at the screen; "רץ אוטומטית" means
there is nothing for them to press; and "לא מפעילים לבד — מדברים איתנו קודם" means they
must talk to the team first — never tell them to go and connect one of those themselves.
Mention features only when they are relevant to what the client asked or said. This is a
conversation, not a product tour — do not recite the list.

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

THEY HAVE JUST FINISHED CONNECTING EVERYTHING ({connected}) — that is why this
conversation is happening now. Open by acknowledging it genuinely: the setup is done, their
90-day plan starts from here, and this conversation is what shapes it. Say it once, briefly,
then get on with listening. Never re-explain the connection process; it's behind them.

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


def readiness(client_id: int) -> dict:
    """Whether the DEEP interview should run yet.

    The gate (2026-08-02): every required connection for the client's PURCHASED
    package must be done first. Two reasons, both real:
    - The interview is far better informed once the platforms are connected —
      it can ask about what it can actually see.
    - It gives the connection walkthrough a reward that isn't a lecture: the
      characterization conversation (and the 90-day clock) genuinely waits for
      it, so the urgency we express is true.

    Fails CLOSED on an unresolved package: `resolved` False means we could not
    read what they bought, so we cannot claim they are finished."""
    from core import client_journey
    status = client_journey.connection_status(client_id)
    return {
        "ready": bool(status["complete"]),
        "completed": is_completed(client_id),
        "connection_status": status,
    }


CONNECTION_NUDGE_SYSTEM = """You are the uallak team talking to a client who has just logged
in for the very first time. Their account is live but their integrations are not connected
yet, and ONE message from you decides whether they finish today or drift for a week.

Say, warmly and in their language:
- A short welcome. You are their team, never an AI or a bot.
- What the ONE next step is, named specifically (`next_step`), and that it takes a couple of
  minutes. Point them at the connection cards on this screen.
- WHY today: their 90-day plan and the in-depth strategy conversation with us both start
  only once EVERYTHING is connected — so finishing today means starting today instead of
  losing a week. State it as a fact about how we work, NEVER as a deadline, a penalty, a
  threat, or a countdown. No false scarcity.
- That you're right here if anything gets stuck.

Do NOT list every step. One next step, one reason, one offer of help.

LANGUAGE: {language_rule}

HARD LIMITS: max 4 sentences. At most one emoji. No bullet lists.

Return JSON only: {{"message": "..."}}"""


def connection_nudge(client_id: int, language: str = "he") -> dict:
    """The first-login urgency message (handoff Part 4, point 4). Written by
    the LLM against the client's REAL next step, so it names the actual thing
    rather than reading like a form letter.

    Deduped to once per day — a client reloading the dashboard is not asked
    again, and a client who genuinely stalls for a day gets one fresh nudge."""
    from agents.client_agent import get_client, log_communication
    from agents.onboarding_agent import LANGUAGE_RULE
    from core import client_journey

    if _acted_within(client_id, NUDGE_ACTION, 1):
        return {"success": True, "message": "", "sent": False, "reason": "nudged_today"}

    status = client_journey.connection_status(client_id)
    if status["complete"] or not status["next_step"]:
        return {"success": True, "message": "", "sent": False, "reason": "nothing_missing"}

    client = get_client(client_id) or {}
    payload = {
        "client_name": client.get("name", ""),
        "ui_language": language,
        "next_step": status["next_step"],
        "done_count": status["done_count"],
        "total_count": status["total_count"],
        "remaining": status["missing"],
    }
    try:
        result = timed_step(
            AGENT_NAME, "connection_nudge_llm",
            lambda: safe_claude_json_call(
                CONNECTION_NUDGE_SYSTEM.format(language_rule=LANGUAGE_RULE),
                json.dumps(payload, ensure_ascii=False),
                max_tokens=400, client_id=client_id, cost_category="claude_interview"))
        message = (result.get("message") or "").strip()
    except Exception as e:  # includes ClaudeJSONError
        print(f"[{AGENT_NAME}] connection nudge failed for client {client_id}: {e}")
        message = ""

    if not message:
        return {"success": True, "message": "", "sent": False}
    log_communication(client_id, "outbound", "dashboard_chat", message)
    _log_activity(client_id, NUDGE_ACTION,
                  {"next_step": status["next_step"], "remaining": status["missing"]})
    return {"success": True, "message": message, "sent": True,
            "next_step": status["next_step"]}


def _acted_within(client_id: int, action_type: str, days: int) -> bool:
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = (_db().table("client_activity").select("id")
            .eq("client_id", client_id).eq("agent_name", AGENT_NAME)
            .eq("action_type", action_type)
            .gte("created_at", cutoff).limit(1).execute().data)
    return bool(rows)


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

    # The gate: the deep interview waits for every required connection. Only
    # blocks the OPENING turn — an interview already under way when something
    # gets disconnected must be allowed to finish rather than dead-ending
    # mid-sentence.
    if not message and not status["complete"]:
        return {"success": False, "code": "ERR_INTERVIEW_NOT_READY",
                "reply": "", "complete": False,
                "connection_status": status}

    if message:
        # The client's own turn is recorded BEFORE the LLM call, so a failure
        # below never loses what they typed.
        log_communication(client_id, "inbound", "dashboard_chat", message)

    from core.feature_catalog import catalog_for_prompt

    system = INTERVIEW_SYSTEM.format(
        personas=_persona_summary(),
        connected=", ".join(status["connected"]) or "their platforms",
        # The prompt used to instruct the model to explain the platform while
        # telling it nothing about the platform. One catalogue, filtered to this
        # client's package, is the whole fix — and it is the same list feature
        # announcements target from, so the two can't drift.
        feature_catalog=catalog_for_prompt(client_id),
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
