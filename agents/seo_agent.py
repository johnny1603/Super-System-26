"""uallak's organic SEO agent — the strategy-and-content brain for clients on
an organic SEO package (the SEOptimer/SEMrush/Ahrefs tiers in PRICING).

Division of labor (coordinates with, never duplicates):
- website_agent owns the site itself: publishing, editing, technical/site
  standards, plugins. Articles this agent writes go THROUGH
  website_agent.publish_content (drafts by default — human approves in WP).
- seo_tools_service (core/) talks to the client-paid research tool; this
  agent decides what to ask and what the answers mean. No tool connected or
  the tool call fails → the fallback is Claude's own knowledge with web
  search (claude_web_search_call — the market_reality pattern), never a new
  paid data source.
- A future media agent plugs in at get_recent_articles_for_promotion() for
  cross-promotion (site articles adapted for social with links back).

Two approval lanes (deliberate, per business decision):
- ORGANIC STRATEGY (what to target, what to write, backlink priorities)
  routes to JOHNNY via agent_alert + a seo_strategy_proposed activity row —
  clients have no basis to evaluate SEO strategy, so it never enters the
  client-facing suggestions pipeline.
- ROUTINE CONTENT (the articles themselves) follows the normal
  draft-then-human-approves flow through website_agent, like all content.

Backlinks: identifying opportunities only. Outreach/acquisition is
Johnny's personal, human-owned work — this agent must never attempt it.

═══ CONTENT IRON RULES (standing policy, mirror of website_agent's) ═════════
Google penalizes scaled low-quality content, NOT AI authorship — so quality +
human review IS the compliance strategy, and no "AI-detection workaround" may
ever be built here:
1. Every article must be genuinely useful and business-specific (the client's
   real services, audience, city) — nothing generic enough to publish on a
   competitor's site unchanged.
2. Real expertise markers: concrete practical advice; numbers/examples only
   from the provided business context — never invented facts, prices,
   statistics, testimonials, or credentials.
3. Hard volume cap: MAX_ARTICLES_PER_WEEK per client, enforced in code — mass
   production of thin pages is exactly Google's "scaled content abuse" and is
   structurally impossible through this agent.
4. Each piece targets a DIFFERENT topic/keyword (recent topics are checked).
5. All content passes website_agent.content_quality_issues (headings start at
   h2, excerpt present, etc.) before it is allowed near a site.
═════════════════════════════════════════════════════════════════════════════
"""
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

from supabase import create_client as _supabase_client

from core import seo_tools_service as seo_tools
from core import wordpress_service as wp
from core.agent_base import agent_alert, log_step, timed_step
from core.claude_json import ClaudeJSONError, claude_web_search_call, safe_claude_json_call

AGENT_NAME = "seo_agent"
SEO_TOOL_PLATFORM = "seo_tool"

# Iron rule 3 — the anti-"scaled content abuse" brake. Raising this is a
# business decision, not a tweak.
MAX_ARTICLES_PER_WEEK = 2

# Research consumes the client's paid tool units (or a web-search fee) —
# cache aggressively; the organic landscape doesn't move week to week.
RESEARCH_CACHE_DAYS = 7
# The scheduled cycle re-proposes strategy at most this often per client
STRATEGY_MIN_INTERVAL_DAYS = 21

AUDIT_CACHE_SECONDS = 3600
AUDIT_CONTENT_LIMIT = 30
THIN_CONTENT_WORDS = 150   # below this a post is flagged as thin
STALE_CONTENT_DAYS = 180   # published content untouched this long is stale

ARTICLE_WORDS_MIN, ARTICLE_WORDS_MAX = 600, 900

_audit_cache = {}  # client_id -> (fetched_at, audit)

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


def _log_activity(client_id: int, action_type: str, details: dict, result: dict = None):
    _db().table("client_activity").insert({
        "client_id": client_id,
        "agent_name": AGENT_NAME,
        "action_type": action_type,
        "details": details,
        "result": result or {},
    }).execute()


def _recent_activity(client_id: int, action_type: str, within_days: int, limit: int = 10) -> list:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=within_days)).isoformat()
    result = (
        _db().table("client_activity")
        .select("*")
        .eq("client_id", client_id)
        .eq("agent_name", AGENT_NAME)
        .eq("action_type", action_type)
        .gte("created_at", cutoff)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


# ─── Tool connection (admin connects the client-paid tool's API key) ─────────

def connect_tool(client_id: int, tool: str, api_key: str) -> dict:
    """Store the client's SEO tool API key (client_accounts row, same shape as
    every platform connection). Admin-triggered — these tools have no OAuth.
    The key is validated by the first real research call, not here (each
    tool's cheapest 'ping' differs and burns paid units)."""
    tool = (tool or "").strip().lower()
    if tool not in seo_tools.SUPPORTED_TOOLS:
        return {"success": False,
                "errors": [f"tool must be one of {seo_tools.SUPPORTED_TOOLS}"]}
    if not (api_key or "").strip():
        return {"success": False, "errors": ["api_key is required"]}
    from agents.client_agent import upsert_account
    upsert_account(client_id, SEO_TOOL_PLATFORM, tool, api_key.strip(), "active")
    _log_activity(client_id, "seo_tool_connected", {"tool": tool})
    log_step(AGENT_NAME, "connect_tool", f"client {client_id}: {tool}")
    if tool not in seo_tools.IMPLEMENTED_TOOLS:
        return {"success": True, "tool": tool,
                "note": f"stored, but '{tool}' has no API adapter yet — research will "
                        f"use the Claude fallback until one is wired (see the seo skill)"}
    return {"success": True, "tool": tool}


def _get_tool_connection(client_id: int) -> dict:
    result = (
        _db().table("client_accounts")
        .select("*")
        .eq("client_id", client_id)
        .eq("platform", SEO_TOOL_PLATFORM)
        .eq("status", "active")
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else {}


def _client_domain(client_id: int) -> str:
    """The client's domain, from their connected WordPress site URL — the one
    piece of ground truth every research/audit step needs."""
    from agents.website_agent import _get_connection as website_connection
    site_url = (website_connection(client_id).get("account_id") or "")
    return re.sub(r"^https?://", "", site_url).split("/")[0]


# ─── Own-site audit (FREE — reuses the WordPress connection) ──────────────────

def _word_count(html: str) -> int:
    return len(re.sub(r"<[^>]+>", " ", html or "").split())


def audit_site(client_id: int) -> dict:
    """Content inventory + gap flags for the client's own site, at zero cost
    (core WP REST via the existing connection). Complements — does not rerun —
    website_agent's standards check (plugins/pages/alt text live there)."""
    cached = _audit_cache.get(client_id)
    if cached and time.time() - cached[0] < AUDIT_CACHE_SECONDS:
        return cached[1]

    from agents.website_agent import _creds, _get_connection
    connection = _get_connection(client_id)
    if not connection:
        return {"connected": False}
    site_url, username, app_password = _creds(connection)

    log_step(AGENT_NAME, "audit_site", f"client {client_id}: {site_url}")
    audit = {"connected": True, "site_url": site_url, "domain": _client_domain(client_id)}
    try:
        posts = timed_step(
            AGENT_NAME, "audit_fetch_posts",
            lambda: wp.list_content_for_audit(site_url, username, app_password,
                                              "post", limit=AUDIT_CONTENT_LIMIT))
        pages = wp.list_content_for_audit(site_url, username, app_password,
                                          "page", limit=AUDIT_CONTENT_LIMIT)
    except Exception as e:
        # Same contract as get_site_overview: "error" = temporary read issue
        audit["error"] = str(e)
        return audit

    now = datetime.now(timezone.utc)
    inventory, thin, missing_excerpt, stale = [], [], [], []
    for kind, items in (("post", posts), ("page", pages)):
        for item in items:
            title = (item.get("title") or {}).get("rendered", "")
            words = _word_count((item.get("content") or {}).get("rendered", ""))
            has_excerpt = bool(re.sub(r"<[^>]+>", "", (item.get("excerpt") or {}).get("rendered", "")).strip())
            modified = item.get("modified") or ""
            inventory.append({"id": item.get("id"), "kind": kind, "title": title,
                              "status": item.get("status"), "slug": item.get("slug"),
                              "words": words, "date": item.get("date") or "",
                              "modified": modified})
            if item.get("status") == "publish":
                if words < THIN_CONTENT_WORDS:
                    thin.append(title)
                if not has_excerpt:
                    missing_excerpt.append(title)
                try:
                    if (now - datetime.fromisoformat(modified).replace(tzinfo=timezone.utc)).days > STALE_CONTENT_DAYS:
                        stale.append(title)
                except ValueError:
                    pass

    # Posting cadence counts POSTS only, by publish DATE — neither a recently
    # edited page nor a touched-up old post may mask a blog that stopped
    # publishing months ago
    published_posts = [i for i in inventory if i["kind"] == "post" and i["status"] == "publish"]
    last_post_days = None
    if published_posts:
        try:
            newest = max(p["date"] for p in published_posts if p["date"])
            last_post_days = (now - datetime.fromisoformat(newest).replace(tzinfo=timezone.utc)).days
        except ValueError:
            pass

    audit.update({
        "posts_total": len(posts), "pages_total": len(pages),
        "published_posts": len(published_posts),
        "days_since_last_post": last_post_days,
        "thin_content": thin[:10],
        "missing_excerpt": missing_excerpt[:10],
        "stale_content": stale[:10],
        "inventory": inventory,
    })
    _audit_cache[client_id] = (time.time(), audit)
    return audit


# ─── Market research (client-paid tool, Claude web-search fallback) ──────────

FALLBACK_RESEARCH_SYSTEM = """You are an SEO market researcher for uallak, an Israeli
marketing agency. Research the ORGANIC SEARCH landscape for the given client business in
the ISRAELI market (Hebrew searches): who realistically competes with them organically,
what search topics/keyword themes their audience uses, and where the practical content
opportunities are for a small business.

Rules:
- Run at most 3 focused searches, then answer from the results plus your own knowledge.
- Never invent specific search-volume numbers or competitor metrics - describe themes and
  competitors qualitatively.
- Output PLAIN TEXT in English, max 15 short lines, structured as:
  COMPETITORS: (up to 5, with one clause each on why they matter)
  KEYWORD THEMES: (up to 8 themes the business should target, Hebrew keywords welcome)
  OPPORTUNITIES: (up to 4 concrete content opportunities)"""


def run_market_research(client_id: int, force_refresh: bool = False) -> dict:
    """Competitor/keyword research through whichever tool tier the client
    actually pays for; Claude-with-web-search when there is no (working) tool.
    Cached RESEARCH_CACHE_DAYS in client_activity — both paths cost real money
    (the client's tool units / our web-search fee)."""
    if not force_refresh:
        cached = _recent_activity(client_id, "seo_research_completed",
                                  RESEARCH_CACHE_DAYS, limit=1)
        if cached:
            research = (cached[0].get("details") or {}).get("research")
            if research:
                return {**research, "cached": True}

    domain = _client_domain(client_id)
    if not domain:
        return {"success": False, "error": "website not connected — research needs the client's domain"}

    research = None
    connection = _get_tool_connection(client_id)
    tool = connection.get("account_id") or ""
    if connection.get("access_token"):
        log_step(AGENT_NAME, "market_research", f"client {client_id}: via {tool} for {domain}")
        try:
            result = timed_step(
                AGENT_NAME, f"tool_research_{tool}",
                lambda: seo_tools.get_research(tool, connection["access_token"], domain))
            if result.get("supported") and result.get("usable"):
                research = {"success": True, "source": tool, "domain": domain, "data": result}
                if result.get("errors"):
                    log_step(AGENT_NAME, "market_research",
                             f"client {client_id}: partial tool errors: {result['errors']}")
            elif result.get("supported"):
                # Key present but every report failed — likely a bad/expired key;
                # a human should hear about it (the client pays for this tool)
                agent_alert(AGENT_NAME, [
                    f"client {client_id}: {tool} research returned no data "
                    f"({'; '.join(result.get('errors', []))[:300]}) — check the API key/plan; "
                    f"falling back to Claude research"])
        except Exception as e:
            agent_alert(AGENT_NAME, [f"client {client_id}: {tool} research failed: {e} — "
                                     f"falling back to Claude research"])

    if research is None:
        # The deliberate fallback (handoff rule): Claude's knowledge + web
        # search — never a new paid data source
        log_step(AGENT_NAME, "market_research", f"client {client_id}: Claude fallback for {domain}")
        context = _business_context(client_id)
        payload = json.dumps({"business": context, "domain": domain}, ensure_ascii=False)
        try:
            text = timed_step(
                AGENT_NAME, "fallback_research",
                lambda: claude_web_search_call(FALLBACK_RESEARCH_SYSTEM, payload,
                                               max_tokens=1200, client_id=client_id,
                                               cost_category="claude_seo_research"))
            research = {"success": True, "source": "claude_knowledge", "domain": domain,
                        "summary": text}
        except Exception as e:
            agent_alert(AGENT_NAME, [f"client {client_id}: fallback research failed too: {e}"])
            return {"success": False, "error": str(e)}

    _log_activity(client_id, "seo_research_completed",
                  {"research": research, "source": research["source"]})
    return research


def _business_context(client_id: int) -> dict:
    """Who this client is, from the data we already hold: the client row plus
    their sales-chat lead (answers + proposal) — the same grounding the
    support chat uses."""
    from agents.client_agent import get_client
    from agents.support_agent import _latest_lead
    client = get_client(client_id)
    lead = _latest_lead(client.get("email", ""))
    proposal = lead.get("proposal") or {}
    return {
        "name": client.get("name", ""),
        "package": client.get("package", ""),
        "business_summary": proposal.get("business_summary", ""),
        "sales_chat_answers": lead.get("answers") or {},
    }


# ─── Strategy (routes to JOHNNY — never the client-facing suggestions) ────────

STRATEGY_SYSTEM = """You are the organic SEO strategist for uallak, an Israeli marketing
agency serving small/medium businesses. Build a practical organic strategy for ONE client
from: their business context, an audit of their own WordPress site, and market research
(either structured tool data or a research summary).

Ground rules:
- Quality over volume: this agency publishes at most 2 articles per client per week, each
  genuinely useful and business-specific. Never propose mass content production.
- Propose only what a small Israeli business can actually execute. Hebrew-market focus.
- backlink_opportunities are RECOMMENDATIONS for the agency owner to pursue personally
  (directories, partners, local press, suppliers) — never outreach the system would automate.
- Never invent metrics. If the research lacks data for a claim, frame it qualitatively.
- HARD LIMITS: max 6 content_plan items, max 5 on_page_fixes, max 5 backlink_opportunities,
  strategy_summary max 4 sentences, every rationale max 1 sentence.

Return JSON only:
{"strategy_summary": "English, max 4 sentences",
 "content_plan": [{"topic": "Hebrew article topic", "target_keyword": "Hebrew keyword",
                   "rationale": "English, 1 sentence", "priority": 1}],
 "on_page_fixes": ["English, actionable, 1 sentence each"],
 "backlink_opportunities": [{"suggestion": "English, 1 sentence", "why": "English, 1 sentence"}],
 "notes_for_johnny": "English, max 3 sentences"}"""


def build_strategy(client_id: int) -> dict:
    """Audit + research → a proposed organic strategy, logged and alerted to
    Johnny for review. DELIBERATELY not in the client_suggestions pipeline —
    strategy approval is an admin decision; only the resulting articles go
    through a (WP-draft) approval surface."""
    audit = audit_site(client_id)
    if not audit.get("connected"):
        return {"success": False, "error": "website not connected — strategy needs the live site"}
    research = run_market_research(client_id)
    if not research.get("success"):
        return {"success": False, "error": f"research unavailable: {research.get('error')}"}

    payload = {
        "business": _business_context(client_id),
        "site_audit": {k: v for k, v in audit.items() if k != "inventory"},
        "recent_titles": [i["title"] for i in audit.get("inventory", [])[:15]],
        "market_research": research,
    }
    try:
        plan = timed_step(
            AGENT_NAME, "strategy_llm",
            lambda: safe_claude_json_call(STRATEGY_SYSTEM,
                                          json.dumps(payload, ensure_ascii=False),
                                          max_tokens=2000, client_id=client_id,
                                          cost_category="claude_seo"))
    except ClaudeJSONError as e:
        agent_alert(AGENT_NAME, [f"client {client_id}: strategy build failed: {e}"])
        return {"success": False, "error": str(e)}

    _log_activity(client_id, "seo_strategy_proposed",
                  {"plan": plan, "research_source": research.get("source")})
    topics = [item.get("topic", "") for item in (plan.get("content_plan") or [])[:3]]
    agent_alert(AGENT_NAME, [
        f"client {client_id}: organic SEO strategy proposed (research: {research.get('source')}) — "
        f"review the seo_strategy_proposed activity row and execute approved topics via "
        f"POST /api/seo/write-article. First topics: {'; '.join(t for t in topics if t)}"])
    log_step(AGENT_NAME, "build_strategy",
             f"client {client_id}: {len(plan.get('content_plan') or [])} topics proposed")
    return {"success": True, "plan": plan, "research_source": research.get("source")}


# ─── Article writing (iron rules enforced; publishes as WP DRAFT) ─────────────

ARTICLE_SYSTEM = f"""You are a senior Hebrew content writer for uallak, an Israeli marketing
agency, writing ONE article for a small business's WordPress site.

IRON RULES (agency standing policy — Google penalizes scaled low-quality content, not AI
authorship; genuine quality + human review is the compliance strategy):
- The article must be genuinely useful and SPECIFIC to this business (its services, city,
  audience from the business context). If it could run unchanged on a competitor's site,
  it fails.
- Real expertise markers: concrete, practical, experience-flavored advice. Use numbers,
  prices, or examples ONLY if they appear in the provided business context — NEVER invent
  facts, statistics, testimonials, or credentials.
- One clear topic per article, matching the given target keyword naturally (no stuffing).
- It must differ substantively from the recent_titles provided.

FORMAT (violations cause automatic rejection):
- Hebrew. {ARTICLE_WORDS_MIN}-{ARTICLE_WORDS_MAX} words.
- HTML body only: <h2>/<h3> headings (NEVER <h1> — the title is the H1; no level jumps),
  <p>, <ul>/<ol>. NO <img> tags, NO forms, NO inline styles.
- End with a short, natural call-to-action paragraph pointing to the business's services.
- excerpt: Hebrew meta-description, ONE sentence, max 150 characters.
- slug: short English slug, words separated by hyphens.

Return JSON only:
{{"title": "Hebrew title", "slug": "english-slug", "excerpt": "Hebrew, max 150 chars",
 "content_html": "<h2>...</h2><p>...</p>"}}"""


def _articles_this_week(client_id: int) -> int:
    return len(_recent_activity(client_id, "seo_article_generated", 7, limit=MAX_ARTICLES_PER_WEEK + 1))


def write_article(client_id: int, topic: str, target_keyword: str = "", notes: str = "") -> dict:
    """Generate one article and hand it to website_agent as a DRAFT (the human
    sign-off happens in WP, same as all content). Enforces the weekly cap and
    topic dedup in code — the iron rules are policy, not vibes."""
    topic = (topic or "").strip()
    if not topic:
        return {"success": False, "errors": ["topic is required"]}

    from agents.website_agent import content_quality_issues, is_connected, publish_content
    if not is_connected(client_id):
        return {"success": False, "errors": ["website not connected"]}

    used = _articles_this_week(client_id)
    if used >= MAX_ARTICLES_PER_WEEK:
        return {"success": False,
                "errors": [f"weekly article cap reached ({used}/{MAX_ARTICLES_PER_WEEK}) — "
                           "the cap is the anti-scaled-content iron rule, not a soft limit"]}

    recent = [((r.get("details") or {}).get("topic") or "")
              for r in _recent_activity(client_id, "seo_article_generated", 60, limit=10)]
    if any(topic.strip() == t.strip() for t in recent if t):
        return {"success": False, "errors": [f"an article on this exact topic was already "
                                             f"written recently — pick a different angle"]}

    payload = {
        "business": _business_context(client_id),
        "topic": topic,
        "target_keyword": target_keyword,
        "notes": notes,
        "recent_titles": recent[:10],
    }
    log_step(AGENT_NAME, "write_article", f"client {client_id}: '{topic}'")

    user_message = json.dumps(payload, ensure_ascii=False)
    article, issues = None, []
    for attempt in range(2):
        try:
            article = timed_step(
                AGENT_NAME, "article_llm",
                lambda: safe_claude_json_call(ARTICLE_SYSTEM, user_message, max_tokens=4000,
                                              client_id=client_id, cost_category="claude_seo"))
        except ClaudeJSONError as e:
            agent_alert(AGENT_NAME, [f"client {client_id}: article generation failed: {e}"])
            return {"success": False, "errors": [str(e)]}
        issues = content_quality_issues(article.get("content_html", ""),
                                        require_excerpt=True,
                                        has_excerpt=bool((article.get("excerpt") or "").strip()))
        if not (article.get("title") or "").strip():
            issues.append("title is required")
        if not issues:
            break
        # One repair round: feed the named violations back
        user_message = json.dumps({**payload, "previous_attempt_issues": issues},
                                  ensure_ascii=False)
    if issues:
        agent_alert(AGENT_NAME, [f"client {client_id}: article for '{topic}' failed quality "
                                 f"gate twice: {'; '.join(issues)}"])
        return {"success": False, "errors": issues}

    published = publish_content(client_id, {
        "kind": "post",
        "title": article["title"],
        "content": article["content_html"],
        "excerpt": article.get("excerpt", ""),
        "slug": article.get("slug", ""),
        "status": "draft",  # human approves in WP — never auto-publish
    })
    if not published.get("success"):
        return {"success": False, "errors": published.get("errors", ["publish failed"])}

    _log_activity(client_id, "seo_article_generated",
                  {"topic": topic, "target_keyword": target_keyword,
                   "title": article["title"]},
                  {"post_id": published.get("id"), "link": published.get("link", "")})
    log_step(AGENT_NAME, "write_article",
             f"client {client_id}: draft {published.get('id')} created")
    return {"success": True, "post_id": published.get("id"), "title": article["title"],
            "link": published.get("link", ""), "status": "draft",
            "articles_this_week": used + 1, "weekly_cap": MAX_ARTICLES_PER_WEEK}


# ─── Cross-promotion extension point (future media agent) ─────────────────────

def get_recent_articles_for_promotion(client_id: int, limit: int = 5) -> list:
    """PUBLISHED articles ready for social adaptation — the plug-in point for
    a future media agent (adapt for the client's social pages, link back).
    Returns [] when nothing is connected/published; never raises."""
    from agents.website_agent import _creds, _get_connection
    connection = _get_connection(client_id)
    if not connection:
        return []
    site_url, username, app_password = _creds(connection)
    try:
        posts = wp.list_content(site_url, username, app_password, "post", limit=limit * 3)
    except Exception as e:
        log_step(AGENT_NAME, "promotable", f"client {client_id}: fetch failed ({e})")
        return []
    return [{"id": p.get("id"),
             "title": (p.get("title") or {}).get("rendered", ""),
             "link": p.get("link", ""), "modified": p.get("modified")}
            for p in posts if p.get("status") == "publish"][:limit]


# ─── Scheduled cycle ──────────────────────────────────────────────────────────

def run_seo_cycle() -> dict:
    """Scheduled entry (weekly): for every client assigned to this agent
    (client_agents rows — admin assigns via POST /api/clients/{id}/agents),
    refresh the audit + research and propose/refresh strategy for Johnny —
    unless a proposal is still fresh (STRATEGY_MIN_INTERVAL_DAYS). Article
    writing is NOT automatic — Johnny triggers it per approved topic."""
    log_step(AGENT_NAME, "seo_cycle", "starting")
    rows = (
        _db().table("client_agents").select("client_id")
        .eq("agent_name", AGENT_NAME).eq("status", "active")
        .execute().data or []
    )
    summary = {"clients": len(rows), "proposed": 0, "skipped_fresh": 0,
               "skipped_no_site": 0, "failed": 0}
    for row in rows:
        client_id = row["client_id"]
        from agents.website_agent import is_connected
        if not is_connected(client_id):
            summary["skipped_no_site"] += 1
            continue
        if _recent_activity(client_id, "seo_strategy_proposed",
                            STRATEGY_MIN_INTERVAL_DAYS, limit=1):
            summary["skipped_fresh"] += 1
            continue
        result = build_strategy(client_id)  # alerts Johnny on success AND failure paths
        summary["proposed" if result.get("success") else "failed"] += 1
    log_step(AGENT_NAME, "seo_cycle", f"done — {summary}")
    return summary
