"""Client knowledge — the ONE read of what we know about a client's business.

## Why this exists

What uallak knows about a client's business was spread over a dozen places
(sales-chat answers, the proposal, the first-login interview, competitor
research, the SEO tool's cache, journey milestones, suggestion decisions, the
existing-site assessment), and the client chats saw almost none of it. The
interview's notes about best customers, seasonality and what flopped before
were written and never read by anything. See HANDOFF-client-knowledge.md.

Same pattern as feature_catalog / competitor_research: the existing modules
stay the PRODUCERS, and this is the single place a reader asks "what do we
know about this business?". **No new storage** — every section is an existing
row or a derivation of one.

## Rules this module holds

- **Read-only.** No LLM call, no paid call, no writes. Competitor research is
  read CACHE-ONLY (`competitor_research.latest`); `research()`, which can spend
  on web searches, is never called from here.
- **No failure state.** Each source is read on its own; one that is empty or
  fails is simply left out of the profile. No gaps list, no error text — the
  client chats must never get failure context (HANDOFF-chat-data-availability).
- **Not live metrics.** Campaign/site numbers stay in the live platform fields
  the chats already have; this is slower-changing business background.
- **What stays out:** the client's financial answers (profit/loss status,
  revenue trend, recent revenue — a tone risk in a chat with little upside),
  contact details, google_ads_agent's internal 90-day goal (`ads_goal_set` is
  "uallak's own target, not a promise made to the client"), and seo_agent's
  strategy (it routes to Johnny, never the client).
- **Dated.** Research carries its date and is labelled as dated background,
  not current fact.
"""
import re
import time
from datetime import datetime, timezone

SERVICE_NAME = "client_knowledge"

# Per (client, lens), in process — the chats call this every turn
CACHE_SECONDS = 300
_cache = {}

# Competitor research older than the producer's own refresh TTL is still
# useful background for a conversation, as long as it carries its date
RESEARCH_MAX_AGE_DAYS = 90

# The sales chat's base questions that describe the business (dashboard/
# onboarding BASE_QUESTIONS ids). Financial ones are deliberately absent.
SALES_ANSWER_FIELDS = (
    "intro", "business_age", "marketing_budget", "existing_digital", "newsletter_status",
    "main_goal", "biggest_fear", "media_management", "camera_comfort", "media_assets",
    "content_creator_collab", "organic_history", "organic_interest",
)
# Other answers (the sales chat's dynamic questions) are included unless they
# are contact details, internal markers, or look financial
_EXCLUDED_ANSWER_KEYS = {"financial_status", "revenue_trend", "recent_revenue",
                         "name", "phone", "email", "address", "business_tax_id"}
_FINANCIAL_KEY = re.compile(r"revenue|profit|financ|income|loss|debt|turnover|margin|salary|cash",
                            re.IGNORECASE)
MAX_EXTRA_ANSWERS = 8

# Which competitor-research lens each chat speaker reads in full; the general
# concierge gets a short slice of every lens
PERSONA_LENS = {"google": "ads", "meta": "ads", "website": "website", "media": "media"}

# Length caps (characters) — the profile goes into every chat turn
MAX_ANSWER_CHARS = 300
MAX_INTRO_CHARS = 800
MAX_NOTE_CHARS = 300
MAX_RESEARCH_CHARS_FULL = 1500
MAX_RESEARCH_CHARS_SLICE = 450
MAX_MARKET_REALITY_CHARS = 800
MAX_SUGGESTION_TITLES = 8

# client_journey milestone keys -> plain English for the prompt
_MILESTONE_LABELS = {
    "journey_signup": "signed up",
    "journey_subscribed": "subscription started",
    "journey_first_connection": "first platform connected",
    "journey_all_connected": "all required platforms connected",
    "journey_website": "website live",
    "journey_first_campaign": "first ad campaign launched",
    "journey_first_content": "first content produced",
    "journey_first_lead": "first lead received",
    "journey_first_sale": "first deal won from a lead",
}

GOAL_DAYS = 90


def _clip(value, limit: int):
    if isinstance(value, list):
        return [str(v)[:limit] for v in value[:10]]
    return str(value)[:limit] if value is not None else ""


def _attempt(fn, default=None):
    """One source. A failure is logged and the section is left out — never
    surfaced to the reader (see the module docstring)."""
    try:
        return fn()
    except Exception as e:
        print(f"[{SERVICE_NAME}] source read failed (left out): {type(e).__name__}: {e}")
        return default


# ─── Sections ────────────────────────────────────────────────────────────────

def _business(client: dict, lead: dict, package: dict) -> dict:
    answers = lead.get("answers") or {}
    proposal = lead.get("proposal") or {}
    told_us = {}
    for key in SALES_ANSWER_FIELDS:
        if answers.get(key):
            told_us[key] = _clip(answers[key], MAX_INTRO_CHARS if key == "intro" else MAX_ANSWER_CHARS)
    extra = 0
    for key, value in answers.items():
        if (extra >= MAX_EXTRA_ANSWERS or key in told_us or key.startswith("_")
                or key in _EXCLUDED_ANSWER_KEYS or _FINANCIAL_KEY.search(key) or not value):
            continue
        told_us[key] = _clip(value, MAX_ANSWER_CHARS)
        extra += 1

    business = {
        "business_name": client.get("business_name") or "",
        "summary": _clip(proposal.get("business_summary") or "", MAX_INTRO_CHARS),
        "what_they_told_us_in_the_sales_chat": told_us,
    }
    if package:
        business["purchased_package"] = {
            "name": package.get("name", ""),
            "description": package.get("description", ""),
            "services": package.get("recommended_services") or [],
        }
    return {k: v for k, v in business.items() if v}


def _competitors(facts: dict, tool_domains: list) -> list:
    merged, seen = [], set()
    for c in (facts.get("competitors") or []):
        key = (c.get("name") or c.get("url") or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            merged.append({"name": c.get("name", ""), "url": c.get("url", ""),
                           "source": "named by the client"})
    for domain in tool_domains or []:
        if domain.lower() not in seen:
            seen.add(domain.lower())
            merged.append({"url": domain, "source": "their SEO tool (keyword overlap)"})
    return merged[:12]


def _market(client_id: int, proposal: dict, persona_id: str) -> dict:
    from core import competitor_research
    market = {}
    if proposal.get("market_reality"):
        market["market_reality_at_sale_time"] = _clip(proposal["market_reality"],
                                                      MAX_MARKET_REALITY_CHARS)
    own_lens = PERSONA_LENS.get(persona_id)
    lenses = (own_lens,) if own_lens else competitor_research.LENSES
    limit = MAX_RESEARCH_CHARS_FULL if own_lens else MAX_RESEARCH_CHARS_SLICE
    research = []
    for lens in lenses:
        hit = competitor_research.latest(client_id, lens, RESEARCH_MAX_AGE_DAYS)
        if hit:
            research.append({"lens": lens, "researched_on": (hit.get("researched_at") or "")[:10],
                             "summary": _clip(hit["summary"], limit)})
    if research:
        market["competitor_research"] = research
    return market


def _plan(client_id: int, proposal: dict) -> dict:
    from core import client_journey
    plan = {}
    if proposal.get("goals_90_days"):
        plan["goals_90_days"] = proposal["goals_90_days"][:8]
    if proposal.get("kpis"):
        plan["kpis"] = proposal["kpis"]
    if plan:
        plan["note"] = "estimates set at sale time, never guarantees"
    # None = the read failed: say nothing, rather than "not started"
    started = _attempt(lambda: client_journey.connections_completed_at(client_id), None)
    if started is None:
        return plan
    if started:
        try:
            day = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(started.replace("Z", "+00:00"))).days + 1
            plan["cycle_started_on"] = started[:10]
            plan["cycle_day"] = f"day {day} of {GOAL_DAYS}" if day <= GOAL_DAYS \
                else f"{GOAL_DAYS}-day cycle finished ({day} days since start)"
        except Exception:
            pass
    else:
        plan["cycle_day"] = "not started yet (starts when every required platform is connected)"
    return plan


def _history(client_id: int) -> dict:
    from core import client_journey
    history = {}
    journey = _attempt(lambda: client_journey.journey(client_id), {}) or {}
    done = [_MILESTONE_LABELS.get(m["key"], m["key"])
            + (f" ({m['at'][:10]})" if m.get("at") else "")
            for m in journey.get("milestones") or [] if m.get("done")]
    if done:
        history["milestones_reached"] = done

    def decisions(status):
        from agents.engagement_agent import get_suggestions
        return [s.get("title", "") for s in
                get_suggestions(client_id, status=status, limit=MAX_SUGGESTION_TITLES)
                if s.get("title")]
    approved = _attempt(lambda: decisions("approved"), [])
    rejected = _attempt(lambda: decisions("rejected"), [])
    if approved:
        history["suggestions_they_approved"] = approved
    if rejected:
        history["suggestions_they_turned_down"] = rejected

    def assessment():
        from agents.website_agent import latest_assessment
        a = latest_assessment(client_id)
        return {"offered_path": a.get("offered_path"), "assessed_on": (a.get("assessed_at") or "")[:10]} \
            if a and a.get("offered_path") else {}
    site = _attempt(assessment, {})
    if site:
        history["existing_site_assessment"] = site
    return history


# ─── The one read ─────────────────────────────────────────────────────────────

def profile(client_id: int, persona_id: str = "general") -> dict:
    """What we know about this client's business, for grounding a chat.
    `persona_id` picks the competitor-research lens (see PERSONA_LENS). Never
    raises; every section is optional."""
    key = (client_id, persona_id)
    cached = _cache.get(key)
    if cached and cached[0] > time.time() - CACHE_SECONDS:
        return cached[1]

    from agents.client_agent import get_client
    from core import client_journey, competitor_research
    from core.lead_tracking import client_sales_lead
    from agents.interview_agent import captured_facts

    client = _attempt(lambda: get_client(client_id), {}) or {}
    lead = _attempt(lambda: client_sales_lead(client_id=client_id,
                                              columns="answers,proposal")[0], {}) or {}
    proposal = lead.get("proposal") or {}
    package = _attempt(lambda: client_journey._chosen_package(client_id), {}) or {}
    facts = _attempt(lambda: captured_facts(client_id), {}) or {}
    tool_domains = _attempt(lambda: competitor_research._tool_competitor_domains(client_id), [])

    knowledge = {
        "business": _attempt(lambda: _business(client, lead, package), {}),
        "in_their_words": {k: _clip(v, MAX_NOTE_CHARS)
                           for k, v in (facts.get("notes") or {}).items()},
        "competitors": _competitors(facts, tool_domains),
        "market": _attempt(lambda: _market(client_id, proposal, persona_id), {}),
        "plan": _attempt(lambda: _plan(client_id, proposal), {}),
        "history": _attempt(lambda: _history(client_id), {}),
    }
    knowledge = {k: v for k, v in knowledge.items() if v}
    _cache[key] = (time.time(), knowledge)
    return knowledge
