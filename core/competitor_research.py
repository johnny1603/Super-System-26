"""Shared competitor research — the "look before you generate" step.

## Why this is one module and not four

The media, website and ads agents all needed the same thing: who actually
competes with THIS client in THIS niche, and what is visibly working for them.
Four copies of that would mean four prompts drifting apart, four caches, and
four times the web-search fee for the same client. So the research itself lives
here once, and each agent asks for its own LENS of it.

`seo_agent.run_market_research()` deliberately does NOT route through here — it
is tool-FIRST (the client pays for SEMrush/Ahrefs; real data beats a web
search) and already caches its own result. Instead this module READS that
agent's cached row when it exists (`_tool_competitor_domains`), so a client
with a paid tool connected gets real competitor domains folded into every lens
at zero extra cost and zero extra paid units. That is the whole integration —
nothing here ever calls a paid tool itself.

## What it costs and how that is contained

Every uncached call is up to 3 server-side web searches plus tokens
(`claude_web_search_call`, which records both cost components to client_costs).
Research is cached per (client, lens) in `client_activity` for CACHE_DAYS —
a competitive landscape does not move week to week, and the ads lens gets the
shortest TTL because ad creative rotates fastest. An agent that generates ten
images for one client in a day therefore pays for research once a fortnight,
not ten times.

## The honesty rules (mirrors of the ones already standing elsewhere)

- **Never invent metrics.** No made-up ad spend, follower counts, traffic
  numbers or "trending this week" claims. Themes and named competitors,
  described qualitatively — the same rule seo_agent's fallback research and
  media_agent's weekly planner already carry.
- **Inform, never copy.** Every lens prompt says so explicitly. The output is
  direction ("short vertical clips shot in-store outperform studio stills in
  this niche"), never an instruction to reproduce a specific competitor's
  asset, layout or copy.
- **Say when there is nothing.** A niche with no findable competitors returns
  a summary that says so, rather than padding with plausible-sounding filler.
- **Never invent a link.** The media lens also returns EXAMPLES — real content
  in the niche that is working, which media_agent sends to the CLIENT as
  references. Those URLs are verified in code against what the search tool
  actually returned (`_strip_unsourced_examples`) and dropped when they can't
  be, because a fabricated link is a broken promise to a paying client, not
  merely a wrong answer. Zero surviving examples is a normal, honest outcome.

## Grounding: we already know who the competitors are

The sales chat asks every prospect for their strongest competitors by name
("a name or two is gold" — onboarding_agent's MARKET SIGNALS block) and the
proposal carries a `market_reality` paragraph. Both are already on file before
any agent generates anything, so research starts from real names the client
gave us rather than from a cold guess at the niche.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from core.agent_base import log_step
from core.claude_json import claude_web_search_call

SERVICE_NAME = "competitor_research"
ACTIVITY_TYPE = "competitor_research_completed"

# One lens per consuming agent. The lens decides the prompt and the TTL; the
# grounding context is identical for all of them.
LENSES = ("media", "website", "ads")

# Ads creative rotates fastest, site design slowest — TTLs follow that, and all
# of them are long enough that research is a per-client-per-fortnight cost, not
# a per-generation one.
CACHE_DAYS = {"media": 14, "website": 30, "ads": 7}
DEFAULT_CACHE_DAYS = 14

# Cap the research spend per call. claude_web_search_call's own WEB_SEARCH_TOOL
# already caps searches at 3; this caps the answer length.
RESEARCH_MAX_TOKENS = 1400

_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        from supabase import create_client
        _db_instance = create_client(os.environ["SUPABASE_URL"],
                                     os.environ["SUPABASE_SERVICE_KEY"])
    return _db_instance


# ─── Shared rules every lens inherits ─────────────────────────────────────────

_COMMON_RULES = """
Standing rules (all of them absolute):
- START from `competitors_named_by_client` when it is present: those are competitors the
  business OWNER named, with links, and they outrank anything you would find by searching
  the niche cold. Research those specifically before looking wider.
- Run at most 3 focused searches, then answer from what you found plus your own knowledge.
- NEVER invent numbers. No fabricated ad spend, traffic, follower counts, conversion rates
  or "trending right now" claims. Describe what you can actually observe, qualitatively.
- INFORM, NEVER COPY. Describe the DIRECTION that appears to work in this niche so a
  different, original asset can be made. Never instruct anyone to reproduce a specific
  competitor's creative, layout, or wording.
- If you genuinely cannot find competitors for this niche, say so plainly in one line
  instead of padding the answer with generic marketing advice.
- The market is ISRAEL and searches should be Hebrew-first where that is natural.
- Output PLAIN TEXT in English (Hebrew keywords/phrases welcome inside it). No markdown
  headers, no preamble, no closing summary."""

_LENS_SYSTEMS = {
    "media": """You are the creative research lead for uallak, an Israeli marketing agency.
Research what VISUAL CONTENT is visibly working for businesses competing with the given
client, so our creative director can make something original that fits the niche's proven
register — not a copy.

Look at competitors' social profiles, websites and public content. Report what you can
actually see: shot types, settings, formats, and the messaging angles that recur.
""" + _COMMON_RULES + """
Structure, max 18 short lines total:
COMPETITORS: (up to 4 named, one clause each on what their content does well)
VISUAL STYLE: (up to 4 lines — lighting, setting, people vs product, polish level)
FORMATS: (up to 3 lines — which formats/aspect ratios/lengths recur in this niche)
MESSAGING ANGLES: (up to 3 lines — the promises/hooks that recur)
EXAMPLES: (up to 4 lines. Real, currently-public content from creators or businesses in
  THIS niche that is visibly doing well. One per line, exactly:
  creator or business name | full URL | one clause on what makes it work
  A specific video/post URL is best; a creator's profile page is acceptable. ONLY paste a
  URL that appeared in your search results — NEVER build one from a username, never guess
  a video or post id, never link a search-results page. These links are shown to a real
  paying client, so a broken or invented one is worse than no line at all. If you found
  none you can stand behind, write the single line: none found)
GAP: (1-2 lines — what nobody in this niche is doing visually, i.e. our opening)""",

    "website": """You are the web strategy researcher for uallak, an Israeli marketing agency.
Before we build or restructure a client's site, research the sites of businesses that
compete with them for relevant LOCAL searches — and deliberately cover BOTH sides of the
results page:
- the top ORGANIC results, and
- the top PAID/AD results (businesses buying those searches often run newer, better-built
  landing pages precisely because they are paying for the traffic — those are worth
  learning from even when the business itself is smaller).
Say which side each competitor came from; if you could not observe the paid side, say that
explicitly rather than presenting organic results as though they covered it.
""" + _COMMON_RULES + """
Structure, max 16 short lines total:
ORGANIC COMPETITORS: (up to 4 named, with the search that surfaced them)
PAID COMPETITORS: (up to 3 named, or one line saying the paid side was not observable)
SITE STRUCTURE: (up to 4 lines — the pages/sections that recur across the good ones)
DESIGN DIRECTION: (up to 3 lines — layout, imagery, and trust signals that recur; note
  anything that reads as dated so we avoid it)
CONVERSION PATTERNS: (up to 3 lines — how they ask for the enquiry: form placement, phone,
  WhatsApp, pricing transparency)
GAP: (1-2 lines — what the client's site could do that these do not)""",

    "ads": """You are the paid-media research lead for uallak, an Israeli marketing agency.
Before we build a campaign, research what competitors in this niche are actually running
and how the space positions itself, so targeting, messaging and creative are grounded
rather than generic.

Public ad libraries are fair game where they are reachable (Meta Ad Library, Google Ads
Transparency Center) alongside competitor sites and landing pages. Be explicit about what
you could and could not actually see — an unobservable ad library is a finding, not a
reason to guess.
""" + _COMMON_RULES + """
- Never state a competitor's budget, bid, or performance. Those are not observable.
Structure, max 16 short lines total:
COMPETITORS ADVERTISING: (up to 4 named, and where you saw them; say if unobservable)
OFFERS: (up to 4 lines — the offers/incentives that recur in this space)
MESSAGING: (up to 4 lines — the hooks, proof points and objections addressed)
AUDIENCE SIGNALS: (up to 3 lines — who the messaging is visibly aimed at)
SATURATION: (1-2 lines — how crowded this looks and what that implies for a small budget)
GAP: (1-2 lines — the angle nobody is running)""",
}


# ─── Grounding context ────────────────────────────────────────────────────────

def _client_brief(client_id: int) -> dict:
    """Who this client is, reusing seo_agent's context builder rather than a
    second one — it already joins the client row to their sales-chat lead."""
    from agents.seo_agent import _business_context
    context = _business_context(client_id)
    answers = context.get("sales_chat_answers") or {}
    return {
        "name": context.get("name", ""),
        "business_summary": context.get("business_summary", ""),
        # The sales chat asks for competitors by name and writes a market_reality
        # paragraph — research starts from what the client actually told us.
        "sales_chat_answers": answers,
    }


def _market_reality(client_id: int) -> str:
    """The proposal's own competitive-picture paragraph, if one was written."""
    try:
        from agents.client_agent import get_client
        from agents.support_agent import _latest_lead
        lead = _latest_lead((get_client(client_id) or {}).get("email", ""))
        return ((lead.get("proposal") or {}).get("market_reality") or "")[:800]
    except Exception as e:
        print(f"[{SERVICE_NAME}] market_reality lookup failed for client {client_id}: {e}")
        return ""


def _client_supplied_competitors(client_id: int) -> list:
    """Competitors the CLIENT named, from the first-login interview
    (agents/interview_agent.py). The best input of the three sources: the
    sales chat asked while selling them, the SEO tool infers from keyword
    overlap, but this is the business owner naming who they actually lose
    deals to — with links.

    Read through this module ON PURPOSE, so the interview never became its own
    silo: every consuming agent already asks competitor_research for market
    context, so adding a source here reaches all of them at once."""
    try:
        from agents.interview_agent import captured_facts
        facts = captured_facts(client_id)
        return [c for c in (facts.get("competitors") or []) if c.get("name") or c.get("url")][:10]
    except Exception as e:
        print(f"[{SERVICE_NAME}] client-supplied competitor lookup failed for {client_id}: {e}")
        return []


def _tool_competitor_domains(client_id: int) -> list:
    """Real competitor domains from the client's PAID SEO tool — read from
    seo_agent's existing cache, never by calling the tool. Costs nothing and
    consumes none of the client's paid units, so it is safe to fold into every
    lens; empty for the (currently typical) client with no tool connected."""
    try:
        rows = (_db().table("client_activity").select("details")
                .eq("client_id", client_id).eq("agent_name", "seo_agent")
                .eq("action_type", "seo_research_completed")
                .order("created_at", desc=True).limit(1).execute().data or [])
        if not rows:
            return []
        research = (rows[0].get("details") or {}).get("research") or {}
        competitors = ((research.get("data") or {}).get("competitors")) or []
        return [c.get("domain", "") for c in competitors if c.get("domain")][:10]
    except Exception as e:
        print(f"[{SERVICE_NAME}] tool-competitor lookup failed for client {client_id}: {e}")
        return []


# ─── Example links: verified against what search actually returned ────────────
#
# The media lens is the only lens that emits URLs (its EXAMPLES section), because
# it is the only one whose output is shown to the CLIENT rather than folded into
# a generation prompt. A link is a promise: an invented one sends a paying client
# to a 404 in the name of "here's how others do it well". So every URL the model
# writes is checked against the URLs the server-side search really returned, and
# anything unverifiable is dropped from the summary BEFORE it is cached — the
# cache and every consumer therefore only ever hold links that demonstrably exist.
#
# Expect this to drop lines, sometimes all of them. That is the feature working:
# an empty EXAMPLES section is an honest "nothing findable", which is exactly what
# the module's standing "say when there is nothing" rule already demands.

EXAMPLES_HEADER = "EXAMPLES:"
MAX_EXAMPLES = 4
_URL_PATTERN = "http"


def _norm_url(url: str) -> str:
    """Compare-friendly form: no scheme, no www., no trailing slash/punctuation.
    The query string is DELIBERATELY kept — dropping it would make every
    `youtube.com/watch?v=...` look identical, which is precisely the invented-id
    case this is defending against."""
    normalized = (url or "").strip().strip("<>\"'").rstrip(".,;:)]").lower()
    for scheme in ("https://", "http://"):
        if normalized.startswith(scheme):
            normalized = normalized[len(scheme):]
            break
    if normalized.startswith("www."):
        normalized = normalized[4:]
    return normalized.rstrip("/")


def _url_is_sourced(candidate: str, sourced_norms: list) -> bool:
    """True when a real search result IS this URL, or sits underneath it.

    Ancestor matching is one-directional on purpose: a creator PROFILE the model
    quotes is accepted when search returned one of that profile's pages (the
    profile provably exists), but a longer URL than anything search returned is
    rejected — that is what a guessed video id looks like."""
    candidate_norm = _norm_url(candidate)
    if not candidate_norm or "/" not in candidate_norm:
        return False  # a bare domain is not an example of anything
    return any(source == candidate_norm
               or source.startswith(candidate_norm + "/")
               or source.startswith(candidate_norm + "?")
               for source in sourced_norms)


def _extract_urls(line: str) -> list:
    return [token.strip().strip("<>\"'").rstrip(".,;:)]")
            for token in line.replace("(", " ").replace(")", " ").split()
            if token.lower().startswith(_URL_PATTERN)]


def _is_section_header(line: str) -> bool:
    """A new ALL-CAPS section starts (COMPETITORS:, GAP: ...), so EXAMPLES ends."""
    head = line.split(":", 1)[0].strip()
    # A URL's own "https:" colon is what keeps an example line from looking like
    # a header: everything before it carries lowercase, so it can't be all-caps.
    return bool(":" in line and head and head == head.upper() and len(head) < 40)


def _strip_unsourced_examples(summary: str, sourced: list) -> tuple:
    """Drop EXAMPLES lines whose links can't be verified. Returns
    (cleaned_summary, dropped_count). Only the EXAMPLES section is touched —
    prose elsewhere is the model's observation, not a clickable promise."""
    if EXAMPLES_HEADER not in (summary or ""):
        return summary, 0
    sourced_norms = [_norm_url(u) for u in (sourced or [])]
    kept_lines, dropped, in_examples = [], 0, False
    for line in summary.splitlines():
        stripped = line.strip()
        if stripped.startswith(EXAMPLES_HEADER):
            in_examples = True
            kept_lines.append(line)
            continue
        if in_examples and _is_section_header(stripped):
            in_examples = False
        if in_examples:
            urls = _extract_urls(stripped)
            if urls and not any(_url_is_sourced(u, sourced_norms) for u in urls):
                dropped += 1
                continue
        kept_lines.append(line)
    return "\n".join(kept_lines), dropped


def parse_examples(summary: str) -> list:
    """The EXAMPLES section as structured rows: [{creator, url, why}].

    Reads whatever survived verification, so a caller never has to re-check a
    link. Returns [] for old cached research written before EXAMPLES existed,
    for a "none found" answer, and for any other lens — all normal."""
    examples, in_examples = [], False
    for raw in (summary or "").splitlines():
        line = raw.strip()
        if line.startswith(EXAMPLES_HEADER):
            in_examples = True
            line = line[len(EXAMPLES_HEADER):].strip()
            if not line:
                continue
        elif not in_examples:
            continue
        elif _is_section_header(line):
            break
        urls = _extract_urls(line)
        if not urls:
            continue
        url = urls[0]
        parts = [p.strip(" -•*|") for p in line.split("|")]
        creator = next((p for p in parts if p and _URL_PATTERN not in p.lower()), "")
        why = next((p for p in reversed(parts)
                    if p and _URL_PATTERN not in p.lower() and p != creator), "")
        examples.append({"creator": creator[:120], "url": url, "why": why[:200]})
        if len(examples) >= MAX_EXAMPLES:
            break
    return examples


def media_examples(client_id: int) -> list:
    """Verified example links for one client's niche, from the MEDIA lens —
    the client-facing entry point. Routes through `research()` so it shares the
    one cached research pass every media generation already pays for, instead of
    being a second, parallel trend-research path. [] when unavailable."""
    result = research(client_id, "media")
    return parse_examples(result.get("summary", "")) if result.get("success") else []


# ─── Cache ────────────────────────────────────────────────────────────────────

def _cached(client_id: int, lens: str) -> dict:
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=CACHE_DAYS.get(lens, DEFAULT_CACHE_DAYS))).isoformat()
    rows = (_db().table("client_activity").select("details,created_at")
            .eq("client_id", client_id).eq("agent_name", SERVICE_NAME)
            .eq("action_type", ACTIVITY_TYPE)
            .gte("created_at", cutoff)
            .order("created_at", desc=True).limit(5).execute().data or [])
    for row in rows:
        details = row.get("details") or {}
        if details.get("lens") == lens and details.get("summary"):
            return {"success": True, "lens": lens, "summary": details["summary"],
                    "tool_competitors": details.get("tool_competitors") or [],
                    "client_named_competitors": details.get("client_named_competitors") or [],
                    "researched_at": row.get("created_at", ""), "cached": True}
    return {}


# ─── The one entry point ──────────────────────────────────────────────────────

def research(client_id: int, lens: str, extra_context: dict = None,
             force_refresh: bool = False) -> dict:
    """Competitor research for one client through one lens.

    Returns {"success", "lens", "summary" (plain text), "cached", ...}. NEVER
    raises: research is an ENRICHMENT step for agents that must still produce
    output without it, so a failure comes back as success=False with a reason
    and the caller carries on unenriched. That is deliberate — a web-search
    outage must not stop a client's content from being made.
    """
    if lens not in LENSES:
        return {"success": False, "lens": lens,
                "error": f"lens must be one of {LENSES}"}

    if not force_refresh:
        try:
            hit = _cached(client_id, lens)
            if hit:
                return hit
        except Exception as e:
            print(f"[{SERVICE_NAME}] cache read failed for client {client_id}: {e}")

    try:
        brief = _client_brief(client_id)
        tool_competitors = _tool_competitor_domains(client_id)
        client_named = _client_supplied_competitors(client_id)
        payload = {
            "business": brief,
            "market_reality_from_proposal": _market_reality(client_id),
            # Named by the client in the sales chat / found by their paid SEO
            # tool — the prompt is told to start from these before searching wide
            "known_competitor_domains": tool_competitors,
            # Named by the CLIENT in the first-login interview, with links —
            # the strongest of the three sources (see _client_supplied_competitors)
            "competitors_named_by_client": client_named,
        }
        if extra_context:
            payload["additional_context"] = extra_context

        log_step(SERVICE_NAME, "research", f"client {client_id}: {lens} lens")
        # with_sources: the URLs search actually returned. Used to verify the
        # media lens's EXAMPLES links before they are cached or shown to a
        # client (see _strip_unsourced_examples); harmless for other lenses.
        summary, sourced = claude_web_search_call(
            _LENS_SYSTEMS[lens],
            json.dumps(payload, ensure_ascii=False),
            max_tokens=RESEARCH_MAX_TOKENS,
            client_id=client_id,
            cost_category=f"claude_competitor_research_{lens}",
            with_sources=True)
        summary, dropped_examples = _strip_unsourced_examples(summary, sourced)
        if dropped_examples:
            log_step(SERVICE_NAME, "research",
                     f"client {client_id}: dropped {dropped_examples} unverifiable "
                     f"example link(s) from the {lens} lens")
    except Exception as e:  # includes ClaudeJSONError
        # No agent_alert: an enrichment step that fails is a degraded result,
        # not an incident, and the consuming agents log it in their own context.
        print(f"[{SERVICE_NAME}] client {client_id}: {lens} research failed: {e}")
        return {"success": False, "lens": lens, "error": str(e)}

    result = {"success": True, "lens": lens, "summary": summary,
              "tool_competitors": tool_competitors,
              "client_named_competitors": client_named, "cached": False}
    try:
        _db().table("client_activity").insert({
            "client_id": client_id,
            "agent_name": SERVICE_NAME,
            "action_type": ACTIVITY_TYPE,
            "details": {"lens": lens, "summary": summary,
                        "tool_competitors": tool_competitors,
                        "client_named_competitors": client_named,
                        "grounded_in_tool_data": bool(tool_competitors),
                        "grounded_in_client_input": bool(client_named)},
            "result": {},
        }).execute()
    except Exception as e:
        print(f"[{SERVICE_NAME}] cache write failed for client {client_id}: {e}")
    log_step(SERVICE_NAME, "research",
             f"client {client_id}: {lens} done ({len(summary)} chars, "
             f"tool-grounded={bool(tool_competitors)})")
    return result


def summary_for_prompt(client_id: int, lens: str, force_refresh: bool = False) -> str:
    """Convenience wrapper for the common case: the research TEXT to drop into
    a generation prompt, or "" when research is unavailable. Callers branch on
    the empty string rather than handling a dict."""
    result = research(client_id, lens, force_refresh=force_refresh)
    return result.get("summary", "") if result.get("success") else ""
