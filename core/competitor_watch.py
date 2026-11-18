"""מאיה follows the competitors' PUBLIC social presence (2026-11-16, docs/COMPETITOR-WATCH.md).

What it is: a weekly read of what works for the competitors in the client's
industry, what does not, and how THIS client can do better and more
creatively, as inspiration and never as a template.

Only methods the platforms allow, and what each one really gives:
- YouTube: the Data API on our project key (no client connection). A
  competitor channel's recent videos with their PUBLIC view / like / comment
  counts (youtube_service.public_channel_recent, 3 quota units). MEASURED.
- Instagram: Meta's Business Discovery gives public posts + like/comment counts
  of other Business/Creator accounts, but needs `instagram_manage_insights`,
  a scope we do not request. NOT USED until the owner decides (no new scope
  here).
- Facebook pages, TikTok: no API lets an app read other accounts' content or
  numbers with our grants (TikTok's APIs are own-account only). We never scrape
  and never log in as anyone.
- Everything else: the existing competitor research (web search, links verified
  against what the search returned; core/competitor_research media lens).
  OBSERVED, no numbers.

Never copy: no competitor caption, script, creative, logo or trademark is
reproduced. Code drops any line that quotes more than QUOTE_MAX_WORDS words,
any link that was not returned by a source, and vendor names. The ideas
must be original for this client.

Storage: app_settings `competitor_watch:<id>` = the client's watch list + the
latest digest (one fresh digest a week at most). Cleared with the client.
"""
import json
import re
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "competitor_watch"
PREFIX = "competitor_watch:"
LANGS = ("he", "en", "fr", "ar", "ru")
MAX_COMPETITORS = 8
MAX_YOUTUBE_CHANNELS = 3
FRESH_DAYS = 7
QUOTE_MAX_WORDS = 12
PLATFORMS = ("youtube", "instagram", "facebook", "tiktok", "website")
# What each platform gives us today (shown to the client, plainly)
PLATFORM_ACCESS = {"youtube": "measured", "instagram": "needs_permission", "facebook": "not_available",
                   "tiktok": "not_available", "website": "observed"}


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


def pick(table: dict, lang: str) -> str:
    return table.get(lang) or table.get("en") or ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _db():
    from agents.client_agent import _db as db
    return db()


# ─── The watch list ──────────────────────────────────────────────────────────

_YT = re.compile(r"youtube\.com/(?:(@[\w.\-]+)|channel/(UC[\w\-]{20,}))", re.IGNORECASE)


def classify(url: str) -> dict:
    """{"platform", "handle", "channel_id"} for one public link."""
    text = str(url or "").strip()
    low = text.lower()
    match = _YT.search(text)
    if match:
        return {"platform": "youtube", "handle": match.group(1) or "", "channel_id": match.group(2) or ""}
    for platform, host in (("instagram", "instagram.com"), ("facebook", "facebook.com"), ("facebook", "fb.com"),
                           ("tiktok", "tiktok.com")):
        if host in low:
            return {"platform": platform, "handle": "", "channel_id": ""}
    return {"platform": "website" if low.startswith(("http://", "https://")) or "." in low else "", "handle": "",
            "channel_id": ""}


def _clean_url(value) -> str:
    text = str(value or "").strip()[:300]
    if text and not text.lower().startswith(("http://", "https://")):
        text = "https://" + text
    return text if re.match(r"^https?://[^\s/]+\.[^\s]+", text) else ""


def stored(client_id: int) -> dict:
    try:
        rows = (_db().table("app_settings").select("value").eq("key", f"{PREFIX}{int(client_id)}")
                .limit(1).execute().data or [])
    except Exception:
        return {}
    value = rows[0].get("value") if rows else None
    return value if isinstance(value, dict) else {}


def _store(client_id: int, value: dict):
    _db().table("app_settings").upsert({"key": f"{PREFIX}{int(client_id)}", "value": value,
                                        "updated_at": _now().isoformat()}, on_conflict="key").execute()


def _interview_competitors(client_id: int) -> list:
    try:
        from core import competitor_research
        return competitor_research._client_supplied_competitors(client_id)
    except Exception:
        return []


def watch_list(client_id: int) -> list:
    """[{name, urls:[{url, platform, ...}]}] — the client's own additions first,
    then the competitors they named in the interview. At most MAX_COMPETITORS."""
    out, seen = [], set()
    sources = list(stored(client_id).get("competitors") or []) + [
        {"name": c.get("name", ""), "urls": [c.get("url")] if c.get("url") else []} for c in _interview_competitors(client_id)]
    for item in sources:
        name = " ".join(str(item.get("name") or "").split())[:80]
        urls = []
        for raw in item.get("urls") or []:
            url = _clean_url(raw.get("url") if isinstance(raw, dict) else raw)
            if url:
                urls.append({"url": url, **classify(url)})
        key = name.lower() or (urls[0]["url"].lower() if urls else "")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append({"name": name or urls[0]["url"], "urls": urls})
    return out[:MAX_COMPETITORS]


def add(client_id: int, name: str, url: str = "") -> list:
    """The client's card click: one competitor (and optionally one public link) added."""
    value = stored(client_id)
    competitors = list(value.get("competitors") or [])
    clean_name = " ".join(str(name or "").split())[:80]
    link = _clean_url(url)
    for item in competitors:
        if str(item.get("name", "")).lower() == clean_name.lower():
            if link and link not in item.get("urls", []):
                item["urls"] = (item.get("urls") or []) + [link]
            break
    else:
        competitors.append({"name": clean_name, "urls": [link] if link else []})
    value["competitors"] = competitors[:MAX_COMPETITORS]
    value.pop("digest", None)                    # a new competitor = a new read
    _store(client_id, value)
    return watch_list(client_id)


# ─── Reading what is public ──────────────────────────────────────────────────

def _youtube(entry: dict) -> dict:
    from core import youtube_service
    return youtube_service.public_channel_recent(handle=entry.get("handle", ""), channel_id=entry.get("channel_id", ""))


def youtube_observations(competitors: list) -> list:
    """Real public counts for the competitors that have a YouTube channel."""
    out = []
    for competitor in competitors:
        for link in competitor["urls"]:
            if link["platform"] != "youtube" or len(out) >= MAX_YOUTUBE_CHANNELS:
                continue
            try:
                channel = _youtube(link)
            except Exception as e:
                print(f"[{SERVICE_NAME}] youtube read stopped ({type(e).__name__})")
                return out
            if channel.get("videos"):
                out.append({"competitor": competitor["name"], **channel})
    return out


def _web_research(client_id: int) -> tuple:
    """(summary, verified links) from the cached media lens — no new research path."""
    from core import competitor_research
    summary = competitor_research.summary_for_prompt(client_id, "media") or ""
    links = [e.get("url") for e in competitor_research.media_examples(client_id) or [] if e.get("url")]
    return summary, links


# ─── Cleaning what the model wrote ───────────────────────────────────────────

_QUOTED = re.compile(r"[\"“”«»„']([^\"“”«»„']+)[\"“”«»„']")
_LINK = re.compile(r"https?://\S+")


def copies(text: str) -> bool:
    """True when a line quotes a long stretch (a caption or a script) — dropped."""
    return any(len(m.group(1).split()) > QUOTE_MAX_WORDS for m in _QUOTED.finditer(text or ""))


def _line(value, limit: int, allowed_links: set) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if copies(text):
        return ""
    for link in _LINK.findall(text):
        if link.rstrip(".,)") not in allowed_links:
            text = text.replace(link, "")
    try:
        from core import vendor_names
        text = vendor_names.scrub_auto(text)
    except Exception:
        pass
    return re.sub(r"\s{2,}", " ", text).strip()[:limit]


def clean_digest(raw: dict, allowed_links: set, measured_names: set) -> dict:
    raw = raw if isinstance(raw, dict) else {}

    def rows(key, fields, limit):
        out = []
        for item in raw.get(key) or []:
            if not isinstance(item, dict):
                continue
            row = {f: _line(item.get(f), 220, allowed_links) for f in fields}
            if not row[fields[0]]:
                continue
            evidence = str(item.get("evidence") or "").strip().rstrip(".,)")
            row["evidence"] = evidence if evidence in allowed_links else ""
            who = str(item.get("competitor") or "").strip()
            row["basis"] = "measured" if who in measured_names and item.get("uses_numbers") else "observed"
            out.append(row)
        return out[:limit]
    return {"works": rows("works", ["pattern", "why"], 5), "doesnt": rows("doesnt", ["pattern", "why"], 4),
            "better": rows("better", ["idea", "how"], 5)}


DIGEST_SYSTEM = f"""You are מאיה, a senior social media strategist for ONE small Israeli business. You read what
the competitors in their industry publish PUBLICLY and turn it into a short, practical read, in the client's language.
You get: the business, its brand kit, the competitors, REAL public YouTube numbers for some of them
(youtube — views/likes/comments are measured; compare a channel's videos to EACH OTHER, never across
platforms), and web research notes (observed, no numbers).

Write:
- works: up to 5 patterns that visibly work for them (format, hook, length, timing, topic) — each
  {{"pattern", "why", "competitor", "evidence" (ONE url from the data, or ""), "uses_numbers" (true only when
  the why rests on the youtube numbers)}};
- doesnt: up to 4 patterns that visibly do not work (low response relative to their own other posts, stale, generic);
- better: up to 5 ORIGINAL ideas for THIS business to do it better and more creatively — {{"idea", "how"}} —
  in their brand voice, using what is unique about them.

HARD RULES: NEVER copy or closely imitate a competitor's caption, script, visual, creative, slogan, logo or
trademark; never quote more than a few words of theirs; ideas are inspiration, made new. Never invent a number
or a link: numbers only from the youtube data, links only from the data. If the data is thin, say less.
Never name a tool or vendor. Each text ≤ 30 words.
Return JSON only: {{"works": [], "doesnt": [], "better": []}}"""


def build_digest(client_id: int, lang: str = None) -> dict:
    """ONE model call over what was read. Stored; a fresh one (< FRESH_DAYS) is reused."""
    from core import client_language as _cl
    lang = _cl.resolve(client_id, lang)   # the CLIENT's language, never a fixed "he"
    from core import claude_json
    from core.cost_tracker import cost_action
    from core.model_routing import TIER_MODELS
    value = stored(client_id)
    digest = value.get("digest") or {}
    if digest and str(digest.get("built_at", "")) > (_now() - timedelta(days=FRESH_DAYS)).isoformat():
        return digest
    competitors = watch_list(client_id)
    if not competitors:
        raise WatchError("no_competitors")
    youtube = youtube_observations(competitors)
    try:
        summary, web_links = _web_research(client_id)
    except Exception:
        summary, web_links = "", []
    allowed = {v["url"] for c in youtube for v in c["videos"]} | {c["url"] for c in youtube} | set(web_links)
    try:
        from core import branding
        kit = branding.kit_for_prompt(client_id)
    except Exception:
        kit = {}
    payload = {"language": lang, "brand_kit": kit, "competitors": [c["name"] for c in competitors],
               "youtube": [{"competitor": c["competitor"], "subscribers": c.get("subscribers"),
                            "videos": [{k: v.get(k) for k in ("title", "url", "published_at", "duration", "views", "likes", "comments")}
                                       for v in c["videos"]]} for c in youtube],
               "web_research": summary[:3000], "allowed_links": sorted(allowed)}
    with cost_action("competitor_watch", f"competitor-watch-{int(client_id)}"):
        raw = claude_json.safe_claude_json_call(DIGEST_SYSTEM, json.dumps(payload, ensure_ascii=False, default=str),
                                                max_tokens=2500, model=TIER_MODELS["standard"], client_id=client_id,
                                                cost_category="claude_competitor_research_media")
    digest = {"built_at": _now().isoformat(), "language": lang,
              **clean_digest(raw, allowed, {c["competitor"] for c in youtube}),
              "sources": {"youtube_channels": len(youtube), "web": bool(summary)},
              "access": coverage(competitors)}
    if not (digest["works"] or digest["better"]):
        raise WatchError("nothing_found")
    value["digest"] = digest
    _store(client_id, value)
    return digest


def coverage(competitors: list) -> dict:
    """platform → how many watched links and what we can really read there."""
    out = {}
    for competitor in competitors:
        for link in competitor["urls"]:
            platform = link["platform"] or "website"
            entry = out.setdefault(platform, {"links": 0, "access": PLATFORM_ACCESS.get(platform, "observed")})
            entry["links"] += 1
    return out


class WatchError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ─── Words ───────────────────────────────────────────────────────────────────

TEXT = {
    "title": _five("👀 מה קורה אצל המתחרים השבוע", "👀 What's happening with the competitors this week",
                   "👀 Ce qui se passe chez les concurrents cette semaine", "👀 ما يحدث لدى المنافسين هذا الأسبوع",
                   "👀 Что происходит у конкурентов на этой неделе"),
    "works": _five("מה עובד להם:", "What works for them:", "Ce qui marche pour eux :", "ما ينجح لديهم:", "Что у них работает:"),
    "doesnt": _five("מה פחות עובד:", "What works less well:", "Ce qui marche moins :", "ما ينجح أقل:", "Что работает хуже:"),
    "better": _five("איך אנחנו עושים את זה טוב יותר, ובדרך שלכם:", "How we do it better, your way:",
                    "Comment faire mieux, à votre façon :", "كيف نفعلها أفضل وبأسلوبكم:", "Как сделать лучше — по-вашему:"),
    "measured": _five("(נמדד: מספרים ציבוריים מיוטיוב)", "(measured: public YouTube numbers)",
                      "(mesuré : chiffres publics YouTube)", "(مقاس: أرقام يوتيوب العامة)", "(измерено: публичные цифры YouTube)"),
    "limits": _five("מה אני רואה: ביוטיוב, מספרים ציבוריים אמיתיים. באינסטגרם, פייסבוק וטיקטוק אין לנו היום דרך מותרת לקרוא מספרים של חשבונות אחרים, אז שם זו התרשמות מחיפוש ציבורי בלבד. אנחנו לא מעתיקים תוכן של אף אחד; זה השראה בלבד.",
                    "What I can see: on YouTube, real public numbers. On Instagram, Facebook and TikTok we have no permitted way today to read other accounts' numbers, so there it's an impression from public search only. We never copy anyone's content; this is inspiration only.",
                    "Ce que je vois : sur YouTube, de vrais chiffres publics. Sur Instagram, Facebook et TikTok, aucune méthode autorisée aujourd'hui pour lire les chiffres d'autres comptes : c'est donc une impression issue de la recherche publique. Nous ne copions le contenu de personne ; c'est de l'inspiration.",
                    "ما أراه: في يوتيوب أرقام عامة حقيقية. في إنستغرام وفيسبوك وتيك توك لا توجد لدينا اليوم طريقة مسموحة لقراءة أرقام حسابات أخرى، فهناك انطباع من البحث العام فقط. لا ننسخ محتوى أحد؛ هذا إلهام فقط.",
                    "Что я вижу: на YouTube — реальные публичные цифры. В Instagram, Facebook и TikTok у нас сегодня нет разрешённого способа читать цифры чужих аккаунтов, там это впечатление из публичного поиска. Мы ничего не копируем — только вдохновение."),
}


def message(digest: dict, lang: str = "he") -> str:
    lang = lang if lang in LANGS else "he"
    lines = [pick(TEXT["title"], lang)]
    for key, fields in (("works", ("pattern", "why")), ("doesnt", ("pattern", "why")), ("better", ("idea", "how"))):
        rows = digest.get(key) or []
        if not rows:
            continue
        lines.append(pick(TEXT[key], lang))
        for row in rows:
            text = f"• {row[fields[0]]}" + (f" — {row[fields[1]]}" if row.get(fields[1]) else "")
            if row.get("basis") == "measured":
                text += " " + pick(TEXT["measured"], lang)
            if row.get("evidence"):
                text += f" {row['evidence']}"
            lines.append(text)
    lines.append(pick(TEXT["limits"], lang))
    return "\n".join(lines)


def prompt_block(client_id: int) -> dict:
    """`competitor_watch` for the chat: who is followed, what we can read where, the latest read."""
    try:
        competitors = watch_list(client_id)
    except Exception:
        return {}
    out = {"competitors": [c["name"] for c in competitors], "access": coverage(competitors),
           "access_meaning": {"measured": "real public numbers", "needs_permission": "a scope we do not have",
                              "not_available": "no permitted API", "observed": "public web search only"}}
    latest = prompt_lines(client_id)
    if latest:
        out["latest_read"] = latest
    return out


def prompt_lines(client_id: int) -> dict:
    """For the weekly plan and the agents: the latest digest's ideas (no new call)."""
    digest = stored(client_id).get("digest") or {}
    if not digest:
        return {}
    return {"built_at": digest.get("built_at", "")[:10], "works": [r["pattern"] for r in digest.get("works") or []],
            "better_ideas": [r["idea"] for r in digest.get("better") or []],
            "rule": "inspiration only: never copy a competitor's caption, creative or trademark"}
