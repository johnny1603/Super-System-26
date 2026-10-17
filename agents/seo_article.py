"""seo_agent's article-writing pipeline (split out of seo_agent.py, which
keeps the entry point `write_article` and every policy gate: cap, repeat
topic, quality, draft-only).

Why a pipeline instead of one prompt (2026-10-17): the single-call writer saw
only the sales-chat answers, never the client's site, its competitors or what
people searching the topic actually want — so it filled the gaps with generic
filler and, at worst, plausible-sounding invented detail. Now:

  1. GROUND  — read-only, no paid research: client_knowledge (business in the
               client's own words, products, best customers), cached
               competitor research (cache only — never triggers research),
               the site's REAL pages/posts (titles + URLs) and two recent posts
               as tone-of-voice samples, the site's primary language and any
               translation plugin.
  2. INTENT  — ONE web-search call: what the top results for the topic cover
               and what the searcher wants. Its cost (tokens + per-search fee)
               lands in client_costs under the current cost_action.
  3. BRIEF   — angle, reader, the facts we actually have (each traceable to
               the data), the facts that are MISSING, outline, internal links
               chosen only from the real page list, image ideas.
  4. DRAFT   — in the SITE's language.
  5. CRITIQUE — generic filler, repetition, unsupported claims, voice
               mismatch, translated-sounding language, invented details.
  6. REVISE  — applies the critique plus code-detected problems.

Code, not the model, then enforces: only links to the site's own pages or the
link the client asked for survive (others are unwrapped to plain text); the
requested link is present; the shared HTML quality gate passes; every
[placeholder: ...] is collected so the client is TOLD what is missing.

HARD RULE in every prompt: never invent facts, statistics, prices,
testimonials, awards, dates, materials or processes. A needed fact that is not
in the data becomes a visible [placeholder: ...].

Part 3 (links) will add live 200-verification and categories; Part 2 (media)
consumes `image_suggestions`.
"""
import html as html_lib
import json
import re

from core import wordpress_service as wp
from core.agent_base import log_step, timed_step
from core.claude_json import ClaudeJSONError, claude_web_search_call, safe_claude_json_call

AGENT_NAME = "seo_article"

WORDS_MIN, WORDS_MAX = 600, 1000
SITE_PAGES_LIMIT = 50
SITE_POSTS_LIMIT = 50
VOICE_SAMPLES = 2
VOICE_SAMPLE_CHARS = 1200
RESEARCH_CHARS = 2500
META_MAX_CHARS = 155

LANGUAGE_NAMES = {"he": "Hebrew", "en": "English", "ar": "Arabic", "ru": "Russian",
                  "fr": "French", "es": "Spanish", "de": "German"}

# Translation plugins we can recognise, by plugin id fragment (REST plugin
# list) and by a marker in the public homepage HTML (when the plugin list
# needs rights our WP user may not have).
TRANSLATION_PLUGINS = {
    "wpml": {"plugin": ("sitepress-multilingual-cms",), "html": ("wpml-ls", "sitepress")},
    "polylang": {"plugin": ("polylang",), "html": ("polylang", "pll_")},
    "translatepress": {"plugin": ("translatepress",), "html": ("trp-language-switcher", "translatepress")},
    "weglot": {"plugin": ("weglot",), "html": ("weglot",)},
    "gtranslate": {"plugin": ("gtranslate",), "html": ("gtranslate",)},
}
# How each one treats a post created over REST without a language set:
TRANSLATION_HANDLING = {
    "wpml": "new posts get the site's DEFAULT language on save — the draft is written in that language",
    "polylang": "new posts get the DEFAULT language on save — the draft is written in that language",
    "translatepress": "translates at render time from the default-language original — nothing to set on the post",
    "weglot": "translates at render time from the original — nothing to set on the post",
    "gtranslate": "translates at render time — nothing to set on the post",
}

_NO_INVENTION = """NEVER INVENT: facts, statistics, numbers, prices, discounts, testimonials, customer
quotes, awards, certifications, years in business, dates, materials, ingredients, production
processes, guarantees, delivery times, locations. Use ONLY facts present in the provided data
(business, in_their_words, site pages, voice samples, notes). If a strong article needs a fact
that is missing, write a visible placeholder in the article's language inside square brackets
that starts with the English word placeholder, e.g. [placeholder: what the shoe sole is made of],
instead of guessing. General, widely-known advice about the topic is fine; specific claims
about THIS business are not, unless the data states them."""


class ArticleError(Exception):
    """The pipeline could not produce a usable article (step + reason)."""


# ─── 1. Grounding (read-only, free) ───────────────────────────────────────────

def _text(html_fragment: str) -> str:
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", html_fragment or "")).split())


def _rendered(field) -> str:
    return _text((field or {}).get("rendered", "") if isinstance(field, dict) else str(field or ""))


def _detect_language(homepage_html: str, titles: list) -> dict:
    match = re.search(r"<html[^>]*\blang\s*=\s*[\"']([A-Za-z]{2,3})", homepage_html or "", re.I)
    if match:
        return {"code": match.group(1).lower(), "source": "html_lang"}
    sample = " ".join(titles)
    if re.search(r"[֐-׿]", sample):
        return {"code": "he", "source": "content_script"}
    if re.search(r"[؀-ۿ]", sample):
        return {"code": "ar", "source": "content_script"}
    if re.search(r"[Ѐ-ӿ]", sample):
        return {"code": "ru", "source": "content_script"}
    if re.search(r"[A-Za-z]", sample):
        return {"code": "en", "source": "content_script"}
    return {"code": "he", "source": "assumed"}


def _detect_translation(site_url: str, username: str, app_password: str, homepage_html: str) -> dict:
    hreflangs = sorted(set(re.findall(r"hreflang\s*=\s*[\"']([A-Za-z-]+)", homepage_html or "", re.I)))
    try:
        active = [p.get("plugin", "") for p in wp.list_plugins(site_url, username, app_password)
                  if p.get("status") == "active"]
        for name, marks in TRANSLATION_PLUGINS.items():
            if any(frag in plugin for plugin in active for frag in marks["plugin"]):
                return {"plugin": name, "method": "plugins_api", "hreflang": hreflangs,
                        "handling": TRANSLATION_HANDLING[name]}
        plugins_readable = True
    except Exception as e:  # listing plugins needs admin rights we may not have
        log_step(AGENT_NAME, "translation_detect", f"plugin list unavailable ({e}) — using homepage markers")
        plugins_readable = False
    lowered = (homepage_html or "").lower()
    for name, marks in TRANSLATION_PLUGINS.items():
        if any(m in lowered for m in marks["html"]):
            return {"plugin": name, "method": "homepage_markers", "hreflang": hreflangs,
                    "handling": TRANSLATION_HANDLING[name]}
    return {"plugin": None, "method": "plugins_api" if plugins_readable else "homepage_markers",
            "hreflang": hreflangs,
            "handling": "no translation plugin found — single-language site"}


def site_context(client_id: int) -> dict:
    """The live site as the writer needs it: real pages/posts (title + URL),
    tone-of-voice samples, primary language, translation plugin. Raises
    ArticleError when the site itself cannot be read — writing links and
    voice blind is exactly what this pipeline exists to stop."""
    from agents.website_agent import _creds, _get_connection
    connection = _get_connection(client_id)
    if not connection:
        raise ArticleError("site: website not connected")
    site_url, username, app_password = _creds(connection)
    try:
        pages = wp.rest_get(site_url, "wp/v2/pages", username, app_password,
                            params={"per_page": SITE_PAGES_LIMIT, "status": "publish",
                                    "_fields": "id,title,link"})
        posts = wp.rest_get(site_url, "wp/v2/posts", username, app_password,
                            params={"per_page": SITE_POSTS_LIMIT, "status": "publish",
                                    "_fields": "id,title,link"})
        samples = wp.rest_get(site_url, "wp/v2/posts", username, app_password,
                              params={"per_page": VOICE_SAMPLES, "status": "publish",
                                      "_fields": "title,content"})
    except Exception as e:
        raise ArticleError(f"site: could not read pages/posts: {e}")
    try:
        homepage = wp.fetch_homepage_html(site_url, max_bytes=300_000)
    except Exception as e:
        log_step(AGENT_NAME, "site_context", f"client {client_id}: homepage unreadable ({e})")
        homepage = ""

    def shape(items, kind):
        return [{"kind": kind, "title": _rendered(i.get("title"))[:90], "url": i.get("link", "")}
                for i in items or [] if i.get("link")]
    inventory = shape(pages, "page") + shape(posts, "post")
    language = _detect_language(homepage, [i["title"] for i in inventory])
    translation = _detect_translation(site_url, username, app_password, homepage)
    voice = [{"title": _rendered(s.get("title"))[:90],
              "text": _rendered(s.get("content"))[:VOICE_SAMPLE_CHARS]}
             for s in samples or [] if _rendered(s.get("content"))]
    if not voice:
        # No posts yet: the pages are the only voice the site has
        voice = [{"title": i["title"], "text": ""} for i in inventory[:3]]
    return {"site_url": site_url, "inventory": inventory, "voice_samples": voice,
            "language": language, "translation": translation}


def grounding(client_id: int, persona_id: str = "website") -> dict:
    """What we know about the BUSINESS — client_knowledge (minus account and
    history plumbing) plus the full cached competitor research. Cache-only:
    nothing here can trigger paid research."""
    knowledge = {}
    try:
        from core import client_knowledge
        knowledge = client_knowledge.profile(client_id, persona_id) or {}
    except Exception as e:
        log_step(AGENT_NAME, "grounding", f"client {client_id}: knowledge unavailable ({e})")
    knowledge = {k: v for k, v in knowledge.items() if k not in ("connected_accounts", "history")}
    try:
        from core import competitor_research
        research = competitor_research.latest(client_id, "website") or {}
        if research.get("summary"):
            knowledge["competitor_research"] = {
                "researched_on": (research.get("researched_at") or "")[:10],
                "summary": research["summary"][:RESEARCH_CHARS]}
    except Exception as e:
        log_step(AGENT_NAME, "grounding", f"client {client_id}: research cache unreadable ({e})")
    return knowledge


# ─── 2. Search intent (one web-search call) ───────────────────────────────────

INTENT_SYSTEM = """You research SEARCH INTENT for one article topic for a small business in Israel.
Search the web for the topic (1-2 searches, in the language given), look at what the top results
cover, then answer in English, PLAIN TEXT, max 120 words, as 4 short labelled lines:
WANTS: what the searcher is actually trying to do or decide.
COVERED: the sub-topics/questions the top results all cover.
GAPS: what the top results cover poorly or not at all.
FORMAT: the format that ranks (guide, list, comparison...) and rough length.
Never copy wording from results. No URLs, no statistics."""


def search_intent(client_id: int, topic: str, language_name: str) -> str:
    """'' when the search fails — the brief then works from the data alone."""
    try:
        return timed_step(AGENT_NAME, "search_intent", lambda: claude_web_search_call(
            INTENT_SYSTEM, json.dumps({"topic": topic, "language": language_name, "country": "Israel"},
                                      ensure_ascii=False),
            max_tokens=600, client_id=client_id, cost_category="claude_seo_search"))[:1200]
    except Exception as e:
        log_step(AGENT_NAME, "search_intent", f"client {client_id}: skipped ({e})")
        return ""


# ─── 3-6. Brief → draft → critique → revise ───────────────────────────────────

BRIEF_SYSTEM = """You are the content strategist planning ONE article for a small business's own
website. Plan it from the provided data only.

""" + _NO_INVENTION + """

INTERNAL LINKS: choose 2-4 pages from "site_pages" that a reader of this article would genuinely
want next — copy their "url" EXACTLY; never write any other URL. If "required_link" is set, it
MUST be in the plan (with an anchor idea that fits it).

HARD LIMITS: angle 1 sentence; reader 1 sentence; voice max 2 sentences; facts_available max 12
items, each max 20 words and copied from the data; facts_missing max 5; outline 4-6 sections,
each with max 3 h3; image_ideas max 3.

Return JSON only:
{"angle": "", "reader": "", "voice": "how the site sounds (formality, person, sentence length)",
 "facts_available": ["..."], "facts_missing": ["..."],
 "outline": [{"h2": "", "h3": [""], "covers": "1 sentence"}],
 "internal_links": [{"url": "exact url", "anchor_idea": ""}],
 "image_ideas": [{"description": "", "placement": "featured | after section N"}]}"""

DRAFT_SYSTEM = """You are a senior native-speaker content writer. Write ONE article for a small
business's own website, following the brief exactly, in {language}. Write the way a skilled
native writer in {language} writes for this market — natural idiom and rhythm, never a
translation from English. Match the site's voice described in the brief.

""" + _NO_INVENTION + """

QUALITY: specific to THIS business and its customers — if it could run unchanged on a
competitor's site, it fails. No filler openings ("in today's world..."), no repeated points, no
empty superlatives. Answer what the searcher wants (brief + search_intent), and cover the gaps.

LINKS: link ONLY to the brief's internal_links, with natural anchor text inside sentences
(never "click here"), each at most once. The required_link, if set, MUST appear. No other links.

FORMAT (violations are rejected automatically):
- {words_min}-{words_max} words. HTML body only: <h2>/<h3> (NEVER <h1>; no level jumps), <p>,
  <ul>/<ol>/<li>, <strong>, <a href>. NO <img>, no forms, no inline styles, no scripts.
- End with a short, natural call to action pointing to the business's own services/pages.
- title: max 65 characters. meta_description: ONE sentence, max 155 characters.
- slug: short, Latin letters, words joined by hyphens.
- image_suggestions: max 3, each with a concrete description of a real photo of THIS business's
  product/work (not stock clichés), the alt text (in {language}), and placement.

Return JSON only:
{{"title": "", "meta_description": "", "slug": "", "content_html": "<h2>...</h2><p>...</p>",
 "image_suggestions": [{{"description": "", "alt": "", "placement": "featured | after h2 N"}}]}}"""

CRITIQUE_SYSTEM = """You are a demanding editor and fact-checker reviewing ONE article draft for a
small business's site. Compare it against the data it was allowed to use ("facts_available",
"business", "site_pages", "voice_samples").

Flag ONLY real problems, most important first:
- invented_detail: any specific claim about this business (number, price, material, process,
  award, date, testimonial, guarantee...) NOT supported by the data
- unsupported_claim: a general factual claim stated as certain that needs a source
- generic_filler: sentences that say nothing specific
- repetition: the same point made twice
- voice_mismatch: tone clearly unlike voice_samples
- unnatural_language: phrasing that sounds translated or awkward to a native speaker
- link_problem: an anchor that reads unnaturally or a link that does not fit

HARD LIMITS: max 12 issues; quote max 20 words copied exactly from the draft; fix max 30 words.
Return JSON only:
{"issues": [{"type": "", "quote": "", "fix": ""}], "overall": "1 sentence"}"""

REVISE_SYSTEM = """You are the same native-speaker writer revising your article in {language}.
Apply EVERY listed issue's fix. For invented_detail / unsupported_claim: remove the claim or
replace it with a visible [placeholder: ...] — never keep it and never swap in another guess.
Change nothing that no issue mentions. Keep all rules of the original brief.

""" + _NO_INVENTION + """

LINKS: only the brief's internal_links and the required_link; the required_link MUST appear.
FORMAT: same as before — {words_min}-{words_max} words, <h2>/<h3> (no <h1>, no level jumps),
<p>, <ul>/<ol>/<li>, <strong>, <a href>; no <img>, forms, styles or scripts. title max 65
characters; meta_description one sentence, max 155 characters.

Return JSON only:
{{"title": "", "meta_description": "", "slug": "", "content_html": "",
 "image_suggestions": [{{"description": "", "alt": "", "placement": ""}}]}}"""


def _call(system: str, payload: dict, step: str, client_id: int, max_tokens: int) -> dict:
    return timed_step(AGENT_NAME, step, lambda: safe_claude_json_call(
        system, json.dumps(payload, ensure_ascii=False), max_tokens=max_tokens,
        client_id=client_id, cost_category="claude_seo"))


# ─── Code-side checks (the model is never trusted on these) ──────────────────

_LINK = re.compile(r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
_PLACEHOLDER = re.compile(r"\[\s*placeholder\s*:\s*([^\]]+)\]", re.I)


def _norm_url(url: str) -> str:
    return (url or "").strip().split("#")[0].rstrip("/").lower()


def enforce_links(content_html: str, allowed: set) -> tuple:
    """Unwrap every <a> whose href is not an allowed URL (keeps the anchor
    text). Returns (html, kept_links, removed_urls)."""
    kept, removed = [], []

    def repl(match):
        href, inner = match.group(1), match.group(2)
        if _norm_url(href) in allowed:
            kept.append({"url": href, "anchor": _text(inner)})
            return f'<a href="{html_lib.escape(href, quote=True)}">{inner}</a>'
        removed.append(href)
        return inner
    return _LINK.sub(repl, content_html or ""), kept, removed


def placeholders(content_html: str) -> list:
    return [" ".join(p.split()) for p in _PLACEHOLDER.findall(_text(content_html))]


def headings(content_html: str) -> list:
    return [{"level": int(level), "text": _text(text)}
            for level, text in re.findall(r"<h([23])[^>]*>(.*?)</h\1>", content_html or "", re.I | re.S)]


def _code_issues(article: dict, required_link: str) -> list:
    from agents.website_agent import content_quality_issues
    issues = content_quality_issues(article.get("content_html", ""), require_excerpt=True,
                                    has_excerpt=bool((article.get("meta_description") or "").strip()))
    if not (article.get("title") or "").strip():
        issues.append("title is required")
    if required_link and _norm_url(required_link) not in {
            _norm_url(h) for h in _LINK_HREFS(article.get("content_html", ""))}:
        issues.append(f"the required link {required_link} is missing — link it naturally in a sentence")
    words = len(_text(article.get("content_html", "")).split())
    if words < WORDS_MIN * 0.8:
        issues.append(f"too short ({words} words) — the brief asks for {WORDS_MIN}-{WORDS_MAX}")
    return issues


def _list(value) -> list:
    """Model output that should be a list, defensively (a string or dict
    where a list belongs must not crash the pipeline)."""
    return value if isinstance(value, list) else []


def _LINK_HREFS(content_html: str) -> list:
    return [m.group(1) for m in _LINK.finditer(content_html or "")]


# ─── The one entry point ──────────────────────────────────────────────────────

def build_article(client_id: int, topic: str, target_keyword: str = "", notes: str = "",
                  required_link: str = "", tone: str = "", recent_titles: list = None,
                  market_research: dict = None) -> dict:
    """Run the whole pipeline; returns the finished article and everything we
    learned on the way. Raises ArticleError (step + reason) when no usable
    article can be produced — the caller decides on alerting and wording."""
    site = site_context(client_id)
    language_code = site["language"]["code"]
    language_name = LANGUAGE_NAMES.get(language_code, language_code)
    business = grounding(client_id)
    if market_research:
        business["seo_tool_research"] = market_research
    allowed = {_norm_url(i["url"]) for i in site["inventory"]}
    if required_link:
        allowed.add(_norm_url(required_link))
    log_step(AGENT_NAME, "build_article",
             f"client {client_id}: '{topic}' lang={language_code} ({site['language']['source']}) "
             f"pages={len(site['inventory'])} translation={site['translation']['plugin']}")

    intent = search_intent(client_id, topic, language_name)
    base = {"topic": topic, "target_keyword": target_keyword, "notes": notes, "tone": tone,
            "required_link": required_link, "language": language_name,
            "business": business, "site_pages": site["inventory"],
            "voice_samples": site["voice_samples"], "search_intent": intent,
            "recent_titles": (recent_titles or [])[:10]}

    try:
        brief = _call(BRIEF_SYSTEM, base, "brief", client_id, 2500)
    except ClaudeJSONError as e:
        raise ArticleError(f"brief: {e}")
    brief["internal_links"] = [l for l in _list(brief.get("internal_links"))
                               if isinstance(l, dict) and _norm_url(l.get("url")) in allowed][:5]

    fmt = {"language": language_name, "words_min": WORDS_MIN, "words_max": WORDS_MAX}
    writer_payload = {**base, "brief": brief}
    try:
        draft = _call(DRAFT_SYSTEM.format(**fmt), writer_payload, "draft", client_id, 7000)
    except ClaudeJSONError as e:
        raise ArticleError(f"draft: {e}")

    try:
        critique = _call(CRITIQUE_SYSTEM, {
            "draft": draft, "facts_available": brief.get("facts_available") or [],
            "business": business, "site_pages": site["inventory"],
            "voice_samples": site["voice_samples"]}, "critique", client_id, 2000)
        issues = [i for i in _list(critique.get("issues")) if isinstance(i, dict)][:12]
    except ClaudeJSONError as e:
        log_step(AGENT_NAME, "critique", f"client {client_id}: skipped ({e})")
        issues = []
    code_issues = _code_issues(draft, required_link)
    issues_total = len(issues) + len(code_issues)

    article = draft
    if issues or code_issues:
        try:
            article = _call(REVISE_SYSTEM.format(**fmt), {
                **writer_payload, "previous_draft": draft, "issues": issues,
                "code_detected_problems": code_issues}, "revise", client_id, 7000)
        except ClaudeJSONError as e:
            log_step(AGENT_NAME, "revise", f"client {client_id}: kept the draft ({e})")
            article = draft

    article["content_html"], links, removed = enforce_links(article.get("content_html", ""), allowed)
    if required_link and _norm_url(required_link) not in {_norm_url(l["url"]) for l in links}:
        # The client asked for this link; losing the whole article over it is
        # worse than a plain closing link. Anchor = the page's real title.
        anchor = next((i["title"] for i in site["inventory"]
                       if _norm_url(i["url"]) == _norm_url(required_link) and i["title"]), required_link)
        article["content_html"] += (f'\n<p><a href="{html_lib.escape(required_link, quote=True)}">'
                                    f'{html_lib.escape(anchor)}</a></p>')
        links.append({"url": required_link, "anchor": anchor, "appended_by_code": True})
    remaining = _code_issues(article, required_link)
    if remaining:
        raise ArticleError(f"quality gate after revision: {'; '.join(remaining)}")

    meta = " ".join((article.get("meta_description") or "").split())
    if len(meta) > META_MAX_CHARS:
        meta = meta[:META_MAX_CHARS - 1].rsplit(" ", 1)[0] + "…"
    slug = re.sub(r"[^a-z0-9-]+", "-", (article.get("slug") or "").lower()).strip("-")[:70]
    return {
        "title": " ".join((article.get("title") or "").split())[:90],
        "meta_description": meta,
        "slug": slug,
        "content_html": article["content_html"],
        "headings": headings(article["content_html"]),
        "internal_links": links,
        "removed_links": removed,
        "image_suggestions": [i for i in _list(article.get("image_suggestions")) if isinstance(i, dict)][:3],
        "placeholders": placeholders(article["content_html"]),
        "facts_missing": [str(f) for f in _list(brief.get("facts_missing"))][:5],
        "language": language_code,
        "language_source": site["language"]["source"],
        "translation": site["translation"],
        "search_intent": intent,
        "critique_issues": issues_total,
        "words": len(_text(article["content_html"]).split()),
    }
