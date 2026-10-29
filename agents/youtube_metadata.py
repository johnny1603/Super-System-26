"""The YouTube metadata a client approves BEFORE their video is uploaded —
title, description (with links to their own pages), hashtags, tags, category
and language — for core/agent_actions' publish_youtube_from_media.

Modelled on agents/seo_article.py: ONE entry point (prepare_publish), and
GROUND → DRAFT → CRITIQUE → REVISE, with code — not the model — enforcing
every limit and rule at the end.

  1. GROUND  — context only, nothing paid: the client's request text, the
               Drive file name, client_knowledge (business, offering, tone,
               their target search phrases), CACHED competitor research
               (competitor_research.latest — never triggers research), the
               client's REAL page inventory (site pages via seo_article's
               site_context + published landing pages), and at most ONE
               youtube_service.search_videos for niche phrasing — cached here
               per query, skipped when the key is missing or the shared daily
               search brake (protecting the upload quota) says no.
  2. DRAFT   — or ONE short clarifying question when the request, the file
               name and the business data do not say what the video shows.
  3. CRITIQUE — misleading title, invented detail, stuffing, unnatural
               language, weak opening, links that do not fit.
  4. REVISE  — one pass, applying the critique plus code-detected problems
               (and, for a client's "change the title…", their instruction).

THE MODEL NEVER SEES THE VIDEO. No frames, no audio, no transcript, no
analysis — metadata comes from context only. "Watching the video" is a
deliberate future premium-tier feature, not built (CLAUDE.md).

Code-enforced (validate / revalidate_stored): title ≤ 100, description ≤
4,900, tags ≤ 500 characters in YouTube's own count and ≤ 15 tags, 3-5
hashtags, no HTML/control/bidi characters, no vendor names, no number,
price, percentage or award the grounding does not contain, no competitor
names, links only to the client's OWN pages (https, the site's registered
domain, present in the live inventory) tagged with
lead_tracking.tag_youtube_link. revalidate_stored runs again on the STORED
proposal at the client's click.
"""
import json
import re
import time
from urllib.parse import urlsplit

from core.agent_base import log_step, timed_step
from core.domains import registered_domain
from core.video_limits import DESCRIPTION_MAX, TAGS_MAX, TAGS_TOTAL_MAX, TITLE_MAX, tags_length

AGENT_NAME = "youtube_metadata"
COST_CATEGORY = "content"          # client_costs metering bucket (cost_tracker.METER_CATEGORIES)

HASHTAGS_MIN, HASHTAGS_MAX = 3, 5  # YouTube ignores ALL hashtags past 15; we use 3-5
TAG_MAX_CHARS = 40
LINKS_MAX = 3
REVISIONS_MAX = 3                  # client change requests per proposal
BODY_MAX = 1500
CTA_MAX = 200
LABEL_MAX = 60
INVENTORY_TTL = 600
SEARCH_CACHE_TTL = 7 * 86400
RESEARCH_CHARS = 1500

LANGUAGES = ("he", "en", "fr", "ar", "ru")
LANGUAGE_NAMES = {"he": "Hebrew", "en": "English", "fr": "French", "ar": "Arabic", "ru": "Russian"}

# videos.insert categoryId — the assignable ones
CATEGORIES = {"1": "Film & Animation", "2": "Autos & Vehicles", "10": "Music", "15": "Pets & Animals",
              "17": "Sports", "19": "Travel & Events", "20": "Gaming", "22": "People & Blogs",
              "23": "Comedy", "24": "Entertainment", "25": "News & Politics", "26": "Howto & Style",
              "27": "Education", "28": "Science & Technology", "29": "Nonprofits & Activism"}
DEFAULT_CATEGORY = "22"

# Never in a client's video metadata: our vendors, our tools, ourselves
VENDOR_NAMES = ("uallak", "semrush", "ahrefs", "seoptimer", "higgsfield", "heygen", "elevenlabs",
                "instawp", "anthropic", "claude", "chatgpt", "openai", "manychat", "make.com")

_CONTROL = re.compile(r"[\u0000-\u0008\u000b-\u001f\u007f​-‏‪-‮⁦-⁩]")
_HTML = re.compile(r"<[^>]{0,200}>")
# Multi-digit numbers (prices, years, statistics); a single digit ("3 tips")
# is structure, not a fact. Currency and % signs are checked separately.
_NUMBER = re.compile(r"\d[\d.,]*\d")
_CLAIM_WORDS = re.compile(r"(award|winner|certified|guarantee|#1|no\.\s?1|best in|פרס|זוכה|מוסמך|מובטח|"
                          r"אחריות מלאה|מספר 1|הכי טוב)", re.I)
_URL = re.compile(r"https?://[^\s<>\"]+")

_inventory_cache = {}   # client_id -> (time, inventory)
_search_cache = {}      # query -> (time, [titles])


class MetadataError(Exception):
    """No usable metadata could be produced (the LLM step failed)."""


# ─── Fixed client-facing lines ───────────────────────────────────────────────

def _txt(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


REVISION_CAP = _txt(
    "עשינו כבר 3 סבבי שינויים על הכרטיס הזה. אפשר לאשר אותו כמו שהוא, או לבטל ולבקש מחדש.",
    "We've already made 3 rounds of changes to this card. You can approve it as it is, or cancel and ask again.",
    "Nous avons déjà fait 3 séries de modifications sur cette carte. Vous pouvez la valider telle quelle, ou l'annuler et redemander.",
    "أجرينا بالفعل 3 جولات من التعديلات على هذه البطاقة. يمكنكم اعتمادها كما هي، أو إلغاؤها والطلب من جديد.",
    "По этой карточке уже было 3 круга правок. Можно подтвердить её как есть или отменить и запросить заново.")
REFUSED = _txt(
    "לא הצלחתי לנסח כותרת ותיאור שעומדים בכללים של יוטיוב ושלנו. ספרו לי במשפט אחד על מה הסרטון, ואנסה שוב.",
    "I couldn't write a title and description that meet YouTube's rules and ours. Tell me in one sentence what the video is about, and I'll try again.",
    "Je n'ai pas réussi à rédiger un titre et une description conformes aux règles de YouTube et aux nôtres. Dites-moi en une phrase de quoi parle la vidéo, et je réessaie.",
    "لم أتمكن من كتابة عنوان ووصف يلتزمان بقواعد YouTube وقواعدنا. أخبروني بجملة واحدة عمّ يتحدث الفيديو، وسأحاول مجددًا.",
    "Не получилось составить название и описание по правилам YouTube и нашим. Скажите одной фразой, о чём видео, и я попробую снова.")


def pick(texts: dict, lang: str) -> str:
    return texts.get(lang) or texts["he"]


# ─── Seams (patched by tests) ────────────────────────────────────────────────

def _llm(system: str, payload: dict, step: str, client_id: int, max_tokens: int) -> dict:
    """The deliverable runs on DEFAULT_MODEL (claude_json's default), costed to
    the client under the content bucket."""
    from core.claude_json import safe_claude_json_call
    return timed_step(AGENT_NAME, step, lambda: safe_claude_json_call(
        system, json.dumps(payload, ensure_ascii=False, default=str), max_tokens=max_tokens,
        client_id=client_id, cost_category=COST_CATEGORY))


def _knowledge(client_id: int) -> dict:
    try:
        from core import client_knowledge
        knowledge = client_knowledge.profile(client_id, "media") or {}
    except Exception as e:
        log_step(AGENT_NAME, "grounding", f"client {client_id}: knowledge unavailable ({type(e).__name__})")
        return {}
    return {k: v for k, v in knowledge.items() if k not in ("connected_accounts", "history")}


def _research(client_id: int) -> dict:
    """CACHED only — latest() reads what exists; it never runs research."""
    try:
        from core import competitor_research
        research = competitor_research.latest(client_id, "media") or {}
    except Exception:
        return {}
    if not research.get("summary"):
        return {}
    return {"researched_on": (research.get("researched_at") or "")[:10],
            "summary": research["summary"][:RESEARCH_CHARS]}


def _site_pages(client_id: int) -> tuple:
    """(site_url, [{title, url}]) — the live WordPress inventory, or ("", [])."""
    try:
        from agents.seo_article import site_context
        site = site_context(client_id)
        return site.get("site_url", ""), [{"title": i.get("title", ""), "url": i.get("url", "")}
                                          for i in site.get("inventory") or []]
    except Exception as e:
        log_step(AGENT_NAME, "inventory", f"client {client_id}: no site inventory ({type(e).__name__})")
        return "", []


def _landing_pages(client_id: int) -> list:
    try:
        from agents.landing_page_agent import dashboard_payload
        return [{"title": p.get("title", ""), "url": p.get("url", "")}
                for p in dashboard_payload(client_id).get("pages") or []
                if p.get("status") == "published" and p.get("url")]
    except Exception:
        return []


def _search_videos(query: str, language: str) -> list:
    from core import youtube_service
    if not youtube_service.search_available():
        return []
    return youtube_service.search_videos(query, max_results=5, relevance_language=language)


# ─── Grounding ───────────────────────────────────────────────────────────────

def _norm_url(url: str) -> str:
    return (url or "").strip().split("#")[0].rstrip("/").lower()


def inventory(client_id: int) -> dict:
    """The client's OWN pages a description may link to: {"domain", "pages"}.
    Site pages + published landing pages, kept only when https and on the
    site's registered domain. No site → no domain → no links at all."""
    hit = _inventory_cache.get(client_id)
    if hit and hit[0] > time.time() - INVENTORY_TTL:
        return hit[1]
    site_url, pages = _site_pages(client_id)
    domain = registered_domain(urlsplit(site_url).hostname or "") if site_url else ""
    kept, seen = [], set()
    for page in pages + _landing_pages(client_id):
        parts = urlsplit(page.get("url") or "")
        if (domain and parts.scheme == "https" and registered_domain(parts.hostname or "") == domain
                and _norm_url(page["url"]) not in seen):
            seen.add(_norm_url(page["url"]))
            kept.append({"title": str(page.get("title") or "")[:90], "url": page["url"]})
    result = {"domain": domain, "pages": kept[:80]}
    _inventory_cache[client_id] = (time.time(), result)
    return result


def page_allowed(url: str, inv: dict) -> bool:
    parts = urlsplit(url or "")
    return (bool(inv.get("domain")) and parts.scheme == "https"
            and registered_domain(parts.hostname or "") == inv["domain"]
            and _norm_url(url) in {_norm_url(p["url"]) for p in inv.get("pages") or []})


def target_phrases(knowledge: dict) -> list:
    """organic_target_phrases wherever client_knowledge carries it."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "organic_target_phrases":
                    found.extend(re.split(r"[,\n;|]+", " ".join(value) if isinstance(value, list) else str(value)))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(knowledge)
    return [p.strip() for p in found if p.strip()][:10]


def niche_titles(query: str, language: str) -> list:
    """At most ONE search per query, cached; [] whenever it can't run (no key,
    the shared daily search brake, any error) — enrichment, never required."""
    query = (query or "").strip()[:120]
    if not query:
        return []
    hit = _search_cache.get(query)
    if hit and hit[0] > time.time() - SEARCH_CACHE_TTL:
        return hit[1]
    try:
        titles = [v.get("title", "") for v in _search_videos(query, language) if v.get("title")][:5]
    except Exception as e:
        log_step(AGENT_NAME, "niche_search", f"skipped ({type(e).__name__}: {str(e)[:120]})")
        return []
    _search_cache[query] = (time.time(), titles)
    return titles


def grounding(client_id: int, request: str, file_name: str, about: str, language: str) -> dict:
    knowledge = _knowledge(client_id)
    phrases = target_phrases(knowledge)
    inv = inventory(client_id)
    stem = re.sub(r"[_\-.]+", " ", (file_name or "").rsplit(".", 1)[0]).strip()
    return {"request": (request or "")[:600], "about": (about or "")[:300], "file_name": file_name or "",
            "language": LANGUAGE_NAMES.get(language, "Hebrew"), "business": knowledge,
            "target_phrases": phrases, "cached_research": _research(client_id),
            "site_pages": inv["pages"],
            "niche_titles": niche_titles(phrases[0] if phrases else (about or stem), language),
            "categories": CATEGORIES}


# ─── Prompts ─────────────────────────────────────────────────────────────────

_RULES = """You CANNOT see or hear the video. Everything you write comes from "request", "about",
"file_name" and the business data. NEVER INVENT: prices, discounts, statistics, numbers, awards,
certifications, years in business, results, guarantees, names of people or places — only what the
data states. If a strong line needs a fact you don't have, leave it out. Never mention uallak, AI,
software tools or any vendor. Never name competitors. "niche_titles" are real titles of videos in
the niche: learn how people phrase the topic, never copy them and never mention those channels.
YouTube policy: the title and description must honestly describe the video — no clickbait, no
misleading promises, no ALL-CAPS, no keyword lists, no unrelated popular keywords or brand names."""

_FORMAT = """Write everything in {language}, natural native phrasing.
- title: max 100 characters (aim for 50-70); the main keyword — a target_phrase when one truly fits
  the video, else the plainest phrase people search for this subject — near the start.
- description_body: the first 2 lines (each under ~120 characters) say what the viewer gets and use
  the main keyword; then 2-4 short lines using related phrases naturally. No links, no hashtags,
  max 1500 characters.
- cta: ONE short line inviting the viewer to the business's next step.
- links: 0-3 pages from "site_pages" ONLY (url copied exactly) that fit the video's theme, each
  with a short label (max 60 characters). [] when none fits.
- hashtags: 3-5, each "#" followed by one word or words joined without spaces, all relevant.
- tags: 5-12 relevant search phrases (no competitor or unrelated brand names, no repetition).
- category_id: one key from "categories"."""

DRAFT_SYSTEM = """You write the YouTube title, description and tags for ONE video a small business
is uploading to its own channel.

FIRST decide whether the subject is clear. If "request", "about" and "file_name" together do NOT
say what the video shows (e.g. file_name "VID_2031.mp4" and request "upload it"), return ONLY
{{"needs_clarification": true, "question": "<ONE short question in {language} asking what the video
shows>"}}.

Otherwise:
""" + _RULES + "\n\n" + _FORMAT + """

Return JSON only:
{{"needs_clarification": false, "title": "", "description_body": "", "cta": "",
 "links": [{{"url": "", "label": ""}}], "hashtags": [], "tags": [], "category_id": ""}}"""

CRITIQUE_SYSTEM = """You review YouTube metadata a small business will publish, against the data it was
allowed to use ("request", "about", "file_name", "business", "site_pages"). Flag ONLY real problems,
most important first:
- misleading: the title/description promises something the data does not support
- invented_detail: a specific claim (number, price, award, result...) not in the data
- stuffing: keyword lists, repeated keywords, unrelated or competitor brand names in tags
- unnatural_language: sounds translated or awkward to a native speaker
- weak_opening: the first 2 description lines don't say what the viewer gets
- link_mismatch: a linked page does not fit the video
Max 8 issues; fix max 25 words each. Return JSON only:
{"issues": [{"type": "", "field": "", "fix": ""}]}"""

REVISE_SYSTEM = """You revise the YouTube metadata below. Apply every listed issue and every
"code_problems" item. If "client_change_request" is present, apply it exactly — it wins over style
choices, never over the rules. Change nothing else.
""" + _RULES + "\n\n" + _FORMAT + """

Return JSON only:
{{"needs_clarification": false, "title": "", "description_body": "", "cta": "",
 "links": [{{"url": "", "label": ""}}], "hashtags": [], "tags": [], "category_id": ""}}"""


# ─── Code validation (the model is never trusted on these) ──────────────────

def clean_line(value, limit: int) -> str:
    text = _CONTROL.sub(" ", _HTML.sub(" ", str(value or "")))
    return " ".join(text.split())[:limit].strip()


def clean_block(value, limit: int) -> str:
    lines = [" ".join(_CONTROL.sub(" ", _HTML.sub(" ", line)).split())
             for line in str(value or "").replace("\r", "").split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text[:limit].strip()


def clean_hashtag(value) -> str:
    word = re.sub(r"[^\w]", "", str(value or "").lstrip("#"), flags=re.UNICODE)
    return f"#{word[:40]}" if word else ""


def _vendor_hits(text: str) -> list:
    low = text.lower()
    return [v for v in VENDOR_NAMES if v in low]


def _ground_text(ground: dict) -> str:
    return json.dumps({k: ground.get(k) for k in ("request", "about", "file_name", "business",
                                                  "cached_research", "site_pages")},
                      ensure_ascii=False, default=str).lower()


def _competitors(ground: dict) -> list:
    names = []

    def walk(node, under=False):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, under or "competitor" in str(key).lower())
        elif isinstance(node, list):
            for item in node:
                walk(item, under)
        elif under and isinstance(node, str) and 2 < len(node) <= 40 and "http" not in node:
            names.append(node.strip().lower())
    walk(ground.get("business") or {})
    return names[:20]


def compose_description(body: str, cta: str, links: list, hashtags: list) -> str:
    parts = [body.strip()]
    if cta:
        parts.append(cta.strip())
    if links:
        parts.append("\n".join(f"{l['label']}: {l['url']}" for l in links))
    if hashtags:
        parts.append(" ".join(hashtags))
    return "\n\n".join(p for p in parts if p)


def validate(meta: dict, ground: dict, inv: dict, language: str) -> tuple:
    """(clean metadata, hard problems). Fixable-by-code things are fixed here
    (sanitising, trimming tags/hashtags to the limits, dropping links outside
    the inventory); what only a rewrite can fix becomes a problem."""
    from core.lead_tracking import tag_youtube_link
    problems = []
    title = clean_line(meta.get("title"), 1000)
    if not title:
        problems.append("title is empty")
    elif len(title) > TITLE_MAX:
        problems.append(f"title is {len(title)} characters — max {TITLE_MAX}")
    letters = [c for c in title if c.isalpha() and c.isascii()]
    if len(letters) > 8 and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        problems.append("title is in capitals — write it normally")
    body = clean_block(meta.get("description_body"), 10000)
    if not body:
        problems.append("description_body is empty")
    elif len(body) > BODY_MAX:
        problems.append(f"description_body is {len(body)} characters — max {BODY_MAX}")
    cta = clean_line(meta.get("cta"), CTA_MAX)

    hashtags = []
    for raw in meta.get("hashtags") if isinstance(meta.get("hashtags"), list) else []:
        tag = clean_hashtag(raw)
        if tag and tag.lower() not in {h.lower() for h in hashtags}:
            hashtags.append(tag)
    hashtags = hashtags[:HASHTAGS_MAX]
    if len(hashtags) < HASHTAGS_MIN:
        problems.append(f"{len(hashtags)} hashtags — write {HASHTAGS_MIN}-{HASHTAGS_MAX}")

    competitors = _competitors(ground)
    tags = []
    for raw in meta.get("tags") if isinstance(meta.get("tags"), list) else []:
        tag = clean_line(str(raw).replace("<", " ").replace(">", " ").replace(",", " "), TAG_MAX_CHARS)
        if not tag or tag.lower() in {t.lower() for t in tags}:
            continue
        if any(c and c in tag.lower() for c in competitors):
            problems.append(f"tag names a competitor: {tag}")
            continue
        tags.append(tag)
    tags = tags[:TAGS_MAX]
    while tags and tags_length(tags) > TAGS_TOTAL_MAX:
        tags.pop()

    links = []
    for raw in meta.get("links") if isinstance(meta.get("links"), list) else []:
        if not isinstance(raw, dict) or len(links) >= LINKS_MAX:
            continue
        url = str(raw.get("url") or "").strip()
        if not page_allowed(url, inv):
            log_step(AGENT_NAME, "links", f"dropped a link outside the inventory: {url[:120]}")
            continue
        page = next(p for p in inv["pages"] if _norm_url(p["url"]) == _norm_url(url))
        label = clean_line(raw.get("label"), LABEL_MAX) or clean_line(page["title"], LABEL_MAX)
        links.append({"page": page["url"], "url": tag_youtube_link(page["url"], title), "label": label})

    ground_text = _ground_text(ground)
    visible = " ".join([title, body, cta])
    for number in set(_NUMBER.findall(visible)):
        if number.strip(".,") and number.strip(".,") not in ground_text:
            problems.append(f"'{number}' is not in the data — remove it (never invent numbers)")
    for claim in set(m.group(0) for m in _CLAIM_WORDS.finditer(visible)):
        if claim.lower() not in ground_text:
            problems.append(f"'{claim}' is a claim the data does not support — remove it")
    if any(sym in visible for sym in ("₪", "$", "€", "%")) and not any(
            sym in ground_text for sym in ("₪", "$", "€", "%")):
        problems.append("a price or percentage that is not in the data — remove it")
    for vendor in _vendor_hits(" ".join([title, body, cta] + tags + hashtags)):
        problems.append(f"never mention '{vendor}'")
    for name in competitors:
        if name and name in (title + " " + body).lower():
            problems.append(f"the title/description names a competitor ({name}) — remove it")

    description = compose_description(body, cta, links, hashtags)
    if len(description) > DESCRIPTION_MAX:
        problems.append(f"description is {len(description)} characters — max {DESCRIPTION_MAX}")
    category = str(meta.get("category_id") or "")
    clean = {"title": title[:TITLE_MAX], "description": description[:DESCRIPTION_MAX],
             "body": body, "cta": cta, "tags": tags, "hashtags": hashtags, "links": links,
             "category_id": category if category in CATEGORIES else DEFAULT_CATEGORY,
             "default_language": language if language in LANGUAGES else "he"}
    return clean, problems


def revalidate_stored(client_id: int, params: dict) -> dict:
    """At the CLICK: the stored proposal must still be exactly what validation
    allows — same keys, sanitised text, limits, every URL in the description
    one of its links, each link still an own page in the live inventory and
    tagged for YouTube. Raises ValueError otherwise."""
    from core.lead_tracking import tag_youtube_link
    allowed = {"drive_file_id", "title", "description", "body", "cta", "tags", "hashtags", "links",
               "category_id", "default_language", "revision"}
    if not isinstance(params, dict) or set(params) - allowed:
        raise ValueError(f"unexpected params: {sorted(set(params or {}) - allowed)}")
    title, description = params.get("title"), params.get("description")
    if not isinstance(title, str) or not title or len(title) > TITLE_MAX or clean_line(title, TITLE_MAX) != title:
        raise ValueError("title invalid")
    if (not isinstance(description, str) or not description or len(description) > DESCRIPTION_MAX
            or clean_block(description, DESCRIPTION_MAX) != description):
        raise ValueError("description invalid")
    tags, hashtags, links = params.get("tags") or [], params.get("hashtags") or [], params.get("links") or []
    if (not isinstance(tags, list) or len(tags) > TAGS_MAX or tags_length(tags) > TAGS_TOTAL_MAX
            or any(not isinstance(t, str) or clean_line(t, TAG_MAX_CHARS) != t or "<" in t or ">" in t for t in tags)):
        raise ValueError("tags invalid")
    if (not isinstance(hashtags, list) or not HASHTAGS_MIN <= len(hashtags) <= HASHTAGS_MAX
            or any(clean_hashtag(h) != h or h not in description for h in hashtags)):
        raise ValueError("hashtags invalid")
    if _vendor_hits(" ".join([title, description] + tags)):
        raise ValueError("vendor name in metadata")
    if not isinstance(links, list) or len(links) > LINKS_MAX:
        raise ValueError("links invalid")
    inv = inventory(client_id)
    tagged = set()
    for link in links:
        if not isinstance(link, dict) or not page_allowed(link.get("page", ""), inv):
            raise ValueError("a link is not one of the client's own pages")
        if link.get("url") != tag_youtube_link(link["page"], title):
            raise ValueError("a link is not tagged for YouTube")
        tagged.add(link["url"])
    if any(url not in tagged for url in _URL.findall(description)):
        raise ValueError("the description contains a URL that is not one of its links")
    body, cta = params.get("body"), params.get("cta", "")
    if (not isinstance(body, str) or not body or len(body) > BODY_MAX or clean_block(body, BODY_MAX) != body
            or not isinstance(cta, str) or clean_line(cta, CTA_MAX) != cta):
        raise ValueError("body or cta invalid")
    if description != compose_description(body, cta, links, hashtags):
        raise ValueError("the description is not the one composed from its parts")
    if params.get("category_id") not in CATEGORIES or params.get("default_language") not in LANGUAGES:
        raise ValueError("category or language invalid")
    if not isinstance(params.get("revision", 0), int) or not 0 <= params.get("revision", 0) <= REVISIONS_MAX:
        raise ValueError("revision invalid")
    return params


# ─── The pipeline ────────────────────────────────────────────────────────────

def _list(value) -> list:
    return value if isinstance(value, list) else []


def _finish(client_id: int, meta: dict, ground: dict, inv: dict, language: str) -> dict:
    """Critique → (one) revise → final validation. {"metadata"} or {"refuse"}."""
    try:
        critique = _llm(CRITIQUE_SYSTEM, {**ground, "metadata": meta}, "critique", client_id, 1200)
        issues = [i for i in _list(critique.get("issues")) if isinstance(i, dict)][:8]
    except Exception as e:
        log_step(AGENT_NAME, "critique", f"client {client_id}: skipped ({type(e).__name__})")
        issues = []
    clean, problems = validate(meta, ground, inv, language)
    if issues or problems:
        try:
            meta = _llm(REVISE_SYSTEM.format(language=ground["language"]),
                        {**ground, "metadata": meta, "issues": issues, "code_problems": problems},
                        "revise", client_id, 2000)
        except Exception as e:
            raise MetadataError(f"revise: {type(e).__name__}: {e}")
        clean, problems = validate(meta, ground, inv, language)
    if problems:
        log_step(AGENT_NAME, "refused", f"client {client_id}: {'; '.join(problems)[:400]}")
        return {"refuse": pick(REFUSED, language)}
    return {"metadata": clean}


def generate(client_id: int, request: str, file_name: str, about: str, language: str) -> dict:
    """-> {"clarify": question} | {"metadata": clean} | {"refuse": text}.
    Raises MetadataError when the LLM step itself fails."""
    ground = grounding(client_id, request, file_name, about, language)
    inv = inventory(client_id)
    try:
        draft = _llm(DRAFT_SYSTEM.format(language=ground["language"]), ground, "draft", client_id, 2000)
    except Exception as e:
        raise MetadataError(f"draft: {type(e).__name__}: {e}")
    if draft.get("needs_clarification"):
        question = clean_line(draft.get("question"), 200)
        if question:
            return {"clarify": question}
    return _finish(client_id, draft, ground, inv, language)


def revise_with_request(client_id: int, previous: dict, change_request: str, request: str,
                        file_name: str, language: str) -> dict:
    """A client's "change the title / shorter description / add this phrase"
    on an open card: one revise call from the stored metadata, then the same
    critique → validation. Same return shape as generate()."""
    ground = grounding(client_id, request, file_name, "", language)
    inv = inventory(client_id)
    meta = {"title": previous.get("title", ""), "description_body": previous.get("body", ""),
            "cta": previous.get("cta", ""), "hashtags": previous.get("hashtags", []),
            "tags": previous.get("tags", []), "category_id": previous.get("category_id", ""),
            "links": [{"url": l.get("page", ""), "label": l.get("label", "")}
                      for l in previous.get("links") or []]}
    try:
        revised = _llm(REVISE_SYSTEM.format(language=ground["language"]),
                       {**ground, "metadata": meta, "issues": [], "code_problems": [],
                        "client_change_request": change_request[:400]}, "client_revise", client_id, 2000)
    except Exception as e:
        raise MetadataError(f"client revise: {type(e).__name__}: {e}")
    return _finish(client_id, revised, ground, inv, language)


def prepare_publish(client_id: int, params: dict, context: dict, open_rows: list) -> dict:
    """agent_actions' prepare hook for publish_youtube_from_media. `params` is
    what the chat model proposed (drive_file_id, optional title / about /
    change_request), already validated; `open_rows` are this action's open
    proposals for the client.
    -> {"params": stored, "supersedes": id|None} | {"clarify": q} | {"refuse": text} | {"existing": row}"""
    language = (context or {}).get("language") or "he"
    language = language if language in LANGUAGES else "he"
    file_id = params["drive_file_id"]
    previous_row = next((r for r in open_rows if (r.get("params") or {}).get("drive_file_id") == file_id), None)
    change = " ".join(x for x in (params.get("change_request", ""),
                                  f"title: {params['title']}" if params.get("title") and previous_row else "",
                                  params.get("about", "") if previous_row else "") if x).strip()
    request = (context or {}).get("message", "")
    file_name = (context or {}).get("file_name", "")

    if previous_row and not change:
        return {"existing": previous_row}
    if previous_row:
        previous = previous_row.get("params") or {}
        revision = int(previous.get("revision") or 0) + 1
        if revision > REVISIONS_MAX:
            return {"refuse": pick(REVISION_CAP, language)}
        outcome = revise_with_request(client_id, previous, change, request, file_name, language)
    else:
        revision = 0
        if params.get("title"):
            request = f"{request}\nThe client wants this title: {params['title']}"
        outcome = generate(client_id, request, file_name, params.get("about", ""), language)

    if "metadata" not in outcome:
        return outcome
    meta = outcome["metadata"]
    # body + cta are stored next to the composed description so a revision
    # edits the parts and the click can check description == compose(parts)
    stored = {"drive_file_id": file_id, "title": meta["title"], "description": meta["description"],
              "body": meta["body"], "cta": meta["cta"], "tags": meta["tags"], "hashtags": meta["hashtags"],
              "links": meta["links"], "category_id": meta["category_id"],
              "default_language": meta["default_language"], "revision": revision}
    return {"params": stored, "supersedes": previous_row.get("id") if previous_row else None}
