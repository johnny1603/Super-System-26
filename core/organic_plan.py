"""Organic growth done in the professional order (2026-11-16, docs/ORGANIC-PLAN.md).

No keyword is proposed to the client before research. The order, each step
stored before the next one may start (app_settings `organic_plan:<id>`):

1. state      what the site shows today and who the competitors are
              (core/seo_report — the free organic report — plus the market
              research competitors; positions only where a tracking source
              really measured them, else "not measured yet").
2. keywords   professional keyword research: the client's OWN suggestions
              (sales chat + what they said) + related searches found by a web
              search + competitor terms, each with intent, volume and
              competition. Volume/competition are REAL only when the paid
              connector is on (core/dataforseo_connector.enabled()); otherwise
              they are labelled tiers ("estimate", judgment-based). Our Google
              Ads developer token is at Explorer access, which Google does NOT
              allow to use Keyword Planner (KeywordPlanIdeaService) — so no
              Google Ads volumes, and no new scope is asked.
3. approve    the client approves a SMALL set (≤ MAX_APPROVED) on a card
              (`approve_organic_keywords`); only those are ever measured.
4. backlinks  the client's earned-link opportunities (seo_report) and, when
              real link data is on, the referring domains of the site and the
              competitors' link gap. Off = judgment-based, said so.
5. strategy   what we do, in what order and why, with a time estimate: an
              INTERNAL target and a COMMITTED one = internal × 1.15, rounded
              UP to the half month. Both labelled estimates; never a ranking.

Only then may the 3-month goals card (`agree_quarter_goals`) be offered with
the organic track; its click starts the clock and captures the dated
BASELINE (core/quarter_goals.capture_baseline).
"""
import json
import math
import re
from datetime import datetime, timezone

SERVICE_NAME = "organic_plan"
PREFIX = "organic_plan:"
LANGS = ("he", "en", "fr", "ar", "ru")
STAGES = ("state", "keywords", "approve", "backlinks", "strategy", "ready")
MAX_CANDIDATES = 20
MAX_APPROVED = 5
MIN_APPROVED = 1
BUFFER = 0.15
INTENTS = ("informational", "commercial", "transactional", "local")
TIERS = ("high", "medium", "low")
PROMISE = re.compile(r"(guarantee|מבטיח|נבטיח|הבטחה ל|first page|עמוד הראשון|מקום (ה)?ראשון|#1|top ?3|top ?10)",
                     re.IGNORECASE)


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


def pick(table: dict, lang: str) -> str:
    return table.get(lang) or table.get("en") or ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _db():
    from agents.client_agent import _db as db
    return db()


# ─── Storage ─────────────────────────────────────────────────────────────────

def current(client_id: int) -> dict:
    try:
        rows = (_db().table("app_settings").select("value").eq("key", f"{PREFIX}{int(client_id)}")
                .limit(1).execute().data or [])
    except Exception:
        return {}
    value = rows[0].get("value") if rows else None
    return value if isinstance(value, dict) else {}


def _store(client_id: int, value: dict) -> dict:
    value = {**value, "updated_at": _now().isoformat()}
    _db().table("app_settings").upsert({"key": f"{PREFIX}{int(client_id)}", "value": value,
                                        "updated_at": value["updated_at"]}, on_conflict="key").execute()
    _block_cache.pop(int(client_id), None)
    return value


def stage(value: dict) -> str:
    """The next step to do (or "ready")."""
    if not value.get("state"):
        return "state"
    if not value.get("research"):
        return "keywords"
    if not (value.get("approved") or {}).get("keywords"):
        return "approve"
    if not value.get("backlinks"):
        return "backlinks"
    if not value.get("strategy"):
        return "strategy"
    return "ready"


def approved_keywords(client_id: int) -> list:
    return list((current(client_id).get("approved") or {}).get("keywords") or [])


# ─── Small pure helpers (tested directly) ────────────────────────────────────

def committed_months(internal: float) -> float:
    """The target we commit to the client: internal + 15%, rounded UP to half a month."""
    internal = max(0.5, min(24.0, float(internal)))
    return math.ceil(internal * (1 + BUFFER) * 2) / 2


def _line(value, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    try:
        from core import seo_report
        text = seo_report.VENDORS.sub("", text)
    except Exception:
        pass
    text = re.sub(r"\s{2,}", " ", text).strip()[:limit]
    return "" if PROMISE.search(text) else text


def _norm(word: str) -> str:
    return " ".join(str(word or "").lower().split())


def clean_candidates(raw, client_words: list, real: dict = None) -> list:
    """The model's candidate list → what we show. The client's own suggestions are
    always kept (marked "yours"); intent/tier values outside the lists are dropped;
    a REAL volume replaces the estimated tier only when one was measured."""
    real = {_norm(k): v for k, v in (real or {}).items()}
    out, seen = [], set()
    items = raw if isinstance(raw, list) else []
    own = [{"keyword": w, "source": "client"} for w in client_words or []]
    for item in own + [i for i in items if isinstance(i, dict)]:
        keyword = _line(item.get("keyword"), 70)
        key = _norm(keyword)
        if len(key) < 2:
            continue
        if key in seen:
            # the model's analysis of one of the client's own words: fold it into that row
            for row in out:
                if _norm(row["keyword"]) == key and row["source"] == "client" and item.get("intent"):
                    row.update(_fields(item, real.get(key)))
            continue
        seen.add(key)
        row = {"keyword": keyword, "source": item.get("source") if item.get("source") in ("client", "related", "competitor") else "related"}
        row.update(_fields(item, real.get(key)))
        out.append(row)
    return out[:MAX_CANDIDATES]


def _fields(item: dict, measured) -> dict:
    out = {"intent": item.get("intent") if item.get("intent") in INTENTS else "",
           "volume": item.get("volume") if item.get("volume") in TIERS else "",
           "competition": item.get("competition") if item.get("competition") in TIERS else "",
           "why": _line(item.get("why"), 160), "basis": "estimate"}
    if isinstance(measured, dict) and measured.get("volume") is not None:
        out.update(monthly_searches=int(measured["volume"]), basis="measured")
        if measured.get("competition") is not None:
            out["competition_index"] = measured["competition"]
    return out


def check_approval(words, candidates: list) -> list:
    """The client's chosen set: only researched candidates, 1..MAX_APPROVED."""
    from core import quarter_goals
    chosen = quarter_goals.clean_keywords(words)
    known = {_norm(c["keyword"]): c["keyword"] for c in candidates or []}
    out = [known[_norm(w)] for w in chosen if _norm(w) in known]
    if not MIN_APPROVED <= len(out) <= MAX_APPROVED or len(out) != len(chosen):
        raise ValueError("keywords must be 1-5 researched candidates")
    return out


# ─── The steps ───────────────────────────────────────────────────────────────

def _client_words(client_id: int) -> list:
    from core import quarter_goals
    return quarter_goals.suggested_keywords(client_id)


def _research(client_id: int) -> dict:
    try:
        from agents import seo_agent
        return seo_agent.run_market_research(client_id) or {}
    except Exception:
        return {}


def _positions(client_id: int, keywords: list) -> dict:
    from core import quarter_goals
    try:
        return quarter_goals._positions(client_id, keywords)
    except Exception:
        return {}


def build_state(client_id: int, lang: str = "he") -> dict:
    """Step 1. Reuses the free organic report (built if missing or stale)."""
    from core import seo_report
    report = seo_report.latest(client_id)
    if not report or not seo_report.fresh_enough(report):
        report = seo_report.build(client_id, "", lang)
    research = _research(client_id)
    data = research.get("data") or {}
    competitors = [str(c.get("domain") or c.get("name") or c) if isinstance(c, dict) else str(c)
                   for c in (data.get("competitors") or [])][:5]
    words = _client_words(client_id)
    positions = _positions(client_id, words)
    value = current(client_id)
    value["state"] = {"built_at": _now().isoformat(), "domain": report.get("domain", ""),
                      "summary": (report.get("report") or {}).get("summary", ""),
                      "findings": (report.get("report") or {}).get("site_state", [])[:6],
                      "competitors": competitors,
                      "ranks_today": positions, "ranks_measured": bool(positions)}
    return _store(client_id, value)


KEYWORD_SEARCH_SYSTEM = """You are a senior SEO consultant doing keyword research for ONE small Israeli business.
Search the web (Google results in Israel, in the client's language) to find:
1. related searches and "people also ask" phrasings around the business's services and area;
2. the phrases the named competitors' pages target (titles, headings);
3. local variants (city / neighbourhood + service).
Never invent a competitor or a number. Run at most 5 focused searches.
Output PLAIN TEXT, max 30 short lines: phrase | where you saw it (related / competitor domain) | one-line note."""

KEYWORD_SYSTEM = f"""You are a senior SEO consultant. From the business, the client's OWN suggested phrases, the competitors and the
research notes, list up to {MAX_CANDIDATES} keyword candidates for organic growth, in the client's language.
For each: keyword; source ("client" for the client's own phrases — ALWAYS include every one of them, "related",
"competitor"); intent (informational | commercial | transactional | local); volume (high | medium | low: YOUR
estimate of relative monthly searches in Israel — never a number); competition (high | medium | low: your
estimate of how hard the first page is); why (≤ 20 words: why it fits THIS business, or why it is risky).
Prefer phrases a small business can realistically win: specific, local, buying intent.
Never promise a ranking. Never name an SEO tool.
Return JSON only: {{"candidates": [{{"keyword": "", "source": "", "intent": "", "volume": "", "competition": "", "why": ""}}]}}"""


def research_keywords(client_id: int, lang: str = "he") -> dict:
    """Step 2. One web search + one structuring call (+ real volumes when on)."""
    from core import claude_json, dataforseo_connector
    from core.cost_tracker import cost_action
    value = current(client_id)
    if not value.get("state"):
        raise PlanError("state_first")
    words = _client_words(client_id)
    payload = {"language": lang, "business": _business(client_id), "client_phrases": words,
               "competitors": value["state"].get("competitors") or [], "domain": value["state"].get("domain", "")}
    with cost_action("organic_keywords", f"organic-keywords-{int(client_id)}"):
        try:
            notes = claude_json.claude_web_search_call(KEYWORD_SEARCH_SYSTEM, json.dumps(payload, ensure_ascii=False),
                                                       max_tokens=1800, client_id=client_id,
                                                       cost_category="claude_seo_research")
        except Exception as e:
            print(f"[{SERVICE_NAME}] client {client_id}: keyword search failed ({type(e).__name__})")
            notes = ""
        from core.model_routing import TIER_MODELS
        raw = claude_json.safe_claude_json_call(
            KEYWORD_SYSTEM, json.dumps({**payload, "research_notes": notes}, ensure_ascii=False, default=str),
            max_tokens=3000, model=TIER_MODELS["standard"], client_id=client_id, cost_category="claude_seo_report")
    candidates = clean_candidates((raw or {}).get("candidates"), words)
    real = {}
    if dataforseo_connector.enabled():
        try:
            real = dataforseo_connector.keyword_volumes([c["keyword"] for c in candidates], lang if lang in ("he", "en", "ar", "ru", "fr") else "he")
            candidates = clean_candidates((raw or {}).get("candidates"), words, real)
        except Exception as e:
            print(f"[{SERVICE_NAME}] client {client_id}: real volumes unavailable ({type(e).__name__})")
    if not candidates:
        raise PlanError("no_candidates")
    value["research"] = {"built_at": _now().isoformat(), "candidates": candidates,
                         "basis": "measured" if real else "judgment"}
    value.pop("approved", None)          # a new research = a new choice
    value.pop("strategy", None)
    return _store(client_id, value)


def approve(client_id: int, keywords: list) -> dict:
    """Step 3, the card's click."""
    value = current(client_id)
    chosen = check_approval(keywords, (value.get("research") or {}).get("candidates") or [])
    value["approved"] = {"keywords": chosen, "at": _now().isoformat()}
    value.pop("strategy", None)
    return _store(client_id, value)


def check_backlinks(client_id: int) -> dict:
    """Step 4. The earned-link opportunities from the report; real link data when on."""
    from core import dataforseo_connector, seo_report
    value = current(client_id)
    report = (seo_report.latest(client_id).get("report") or {})
    out = {"built_at": _now().isoformat(), "opportunities": len(report.get("backlink_opportunities") or []),
           "top": [{"site": b.get("site"), "type": b.get("type")} for b in (report.get("backlink_opportunities") or [])[:5]],
           "basis": "judgment"}
    domain = (value.get("state") or {}).get("domain", "")
    if dataforseo_connector.enabled() and domain:
        try:
            out["own"] = dataforseo_connector.summary(domain)
            out["link_gap"] = dataforseo_connector.link_gap(domain, (value.get("state") or {}).get("competitors") or [])[:20]
            out["basis"] = "measured"
        except Exception as e:
            print(f"[{SERVICE_NAME}] client {client_id}: real link data unavailable ({type(e).__name__})")
    value["backlinks"] = out
    return _store(client_id, value)


STRATEGY_SYSTEM = """You are a senior SEO consultant writing the strategy for ONE small Israeli business, in the client's language.
You get: the site state, the APPROVED keywords (work ONLY on these), the backlink picture and the competitors.
Write: steps — up to 6, in the order we do them; each {"what": ≤ 18 words, "why": ≤ 25 words}; and
internal_months — your honest estimate (a number, 1-12) of how long until these keywords show clear
progress for a site like this, with reason ≤ 30 words. It is an ESTIMATE; never promise a ranking,
a position or a date. Never name an SEO tool.
Return JSON only: {"steps": [{"what": "", "why": ""}], "internal_months": 4, "reason": ""}"""


def build_strategy(client_id: int, lang: str = "he") -> dict:
    """Step 5. One call; the committed target is computed in code."""
    from core import claude_json
    from core.cost_tracker import cost_action
    from core.model_routing import TIER_MODELS
    value = current(client_id)
    if stage(value) not in ("strategy", "ready"):
        raise PlanError(f"{stage(value)}_first")
    payload = {"language": lang, "business": _business(client_id), "state": value["state"],
               "approved_keywords": value["approved"]["keywords"], "backlinks": value["backlinks"]}
    with cost_action("organic_strategy", f"organic-strategy-{int(client_id)}"):
        raw = claude_json.safe_claude_json_call(STRATEGY_SYSTEM, json.dumps(payload, ensure_ascii=False, default=str),
                                                max_tokens=2000, model=TIER_MODELS["standard"], client_id=client_id,
                                                cost_category="claude_seo_report")
    steps = [{"what": _line(s.get("what"), 140), "why": _line(s.get("why"), 180)}
             for s in (raw or {}).get("steps") or [] if isinstance(s, dict)]
    steps = [s for s in steps if s["what"]][:6]
    try:
        internal = max(1.0, min(12.0, float((raw or {}).get("internal_months"))))
    except (TypeError, ValueError):
        internal = 4.0
    if not steps:
        raise PlanError("no_strategy")
    value["strategy"] = {"built_at": _now().isoformat(), "steps": steps, "internal_months": internal,
                         "committed_months": committed_months(internal), "reason": _line((raw or {}).get("reason"), 220),
                         "basis": "estimate"}
    return _store(client_id, value)


def advance(client_id: int, lang: str = "he") -> dict:
    """Run the next AUTOMATIC step (everything except the client's approval)."""
    value = current(client_id)
    step = stage(value)
    runner = {"state": build_state, "keywords": research_keywords, "backlinks": lambda c, l: check_backlinks(c),
              "strategy": build_strategy}.get(step)
    if not runner:
        return value
    return runner(client_id, lang)


def _business(client_id: int) -> dict:
    try:
        from agents import seo_agent
        business = seo_agent._business_context(client_id) or {}
    except Exception:
        return {}
    answers = business.get("sales_chat_answers") or {}
    business["sales_chat_answers"] = {k: v for k, v in answers.items() if "budget" not in str(k).lower()}
    return business


class PlanError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ─── Words ───────────────────────────────────────────────────────────────────

TEXT = {
    "next_state": _five("הצעד הראשון בקידום האורגני הוא דוח מצב: מה האתר מראה היום ומי המתחרים. אכין אותו, ורק אחריו נדבר על ביטויים.",
                        "The first organic step is a state report: what the site shows today and who the competitors are. I'll prepare it; only then do we talk keywords.",
                        "La première étape est un état des lieux : ce que montre le site aujourd'hui et qui sont les concurrents. Je le prépare ; ensuite seulement on parle mots-clés.",
                        "الخطوة الأولى تقرير حالة: ماذا يُظهر الموقع اليوم ومن المنافسون. سأجهّزه، وبعده فقط نتحدث عن الكلمات.",
                        "Первый шаг — отчёт о текущем состоянии: что показывает сайт сегодня и кто конкуренты. Я его подготовлю, и только потом обсудим ключевые фразы."),
    "next_keywords": _five("דוח המצב מוכן. הצעד הבא הוא מחקר ביטויים מקצועי: הביטויים שלכם, החיפושים הקשורים בגוגל ומה שהמתחרים מכוונים אליו, עם נפח, תחרות וכוונה.",
                           "The state report is ready. Next is professional keyword research: your phrases, Google's related searches and what competitors target, with volume, competition and intent.",
                           "L'état des lieux est prêt. Ensuite : une recherche de mots-clés professionnelle — vos expressions, les recherches associées de Google et ce que visent les concurrents, avec volume, concurrence et intention.",
                           "تقرير الحالة جاهز. التالي بحث كلمات احترافي: عباراتكم، وعمليات البحث المرتبطة في Google وما يستهدفه المنافسون، مع الحجم والمنافسة والنية.",
                           "Отчёт готов. Дальше — профессиональный подбор фраз: ваши фразы, связанные запросы Google и то, на что нацелены конкуренты, с объёмом, конкуренцией и намерением."),
    "next_approve": _five("המחקר מוכן. עכשיו בוחרים יחד סט קטן (עד 5 ביטויים); רק עליהם נמדוד ונעבוד.",
                          "The research is ready. Now we choose a small set together (up to 5 phrases); only those are measured and worked on.",
                          "La recherche est prête. Choisissons ensemble un petit ensemble (5 expressions au plus) ; seules celles-ci seront mesurées et travaillées.",
                          "البحث جاهز. الآن نختار معًا مجموعة صغيرة (حتى 5 عبارات)؛ عليها فقط نقيس ونعمل.",
                          "Исследование готово. Теперь вместе выбираем небольшой набор (до 5 фраз); только их измеряем и продвигаем."),
    "next_backlinks": _five("הביטויים סוכמו ✓ הצעד הבא: בדיקת הקישורים לאתר שלכם ולמתחרים.",
                            "The phrases are agreed ✓ Next: checking the links to your site and to the competitors.",
                            "Les expressions sont validées ✓ Ensuite : l'analyse des liens vers votre site et vers les concurrents.",
                            "تم الاتفاق على العبارات ✓ التالي: فحص الروابط إلى موقعكم وإلى المنافسين.",
                            "Фразы согласованы ✓ Дальше: проверка ссылок на ваш сайт и на конкурентов."),
    "next_strategy": _five("הצעד הבא: האסטרטגיה, מה עושים, באיזה סדר ולמה, עם הערכת זמן.",
                           "Next: the strategy — what we do, in what order and why, with a time estimate.",
                           "Ensuite : la stratégie — quoi faire, dans quel ordre et pourquoi, avec une estimation de durée.",
                           "التالي: الاستراتيجية — ماذا نفعل وبأي ترتيب ولماذا، مع تقدير زمني.",
                           "Дальше: стратегия — что делаем, в каком порядке и почему, с оценкой сроков."),
    "ready": _five("התוכנית האורגנית מוכנה. אפשר לסכם את היעדים ל-3 החודשים.",
                   "The organic plan is ready. We can agree the 3-month goals.",
                   "Le plan organique est prêt. Nous pouvons convenir des objectifs à 3 mois.",
                   "الخطة العضوية جاهزة. يمكننا الاتفاق على أهداف الأشهر الثلاثة.",
                   "Органический план готов. Можно согласовать цели на 3 месяца."),
    # Only the COMMITTED estimate reaches the client; the internal one (and the
    # 15% buffer between them) never does (2026-11-19).
    "estimate_line": _five("הערכה: תזוזה ברורה בביטויים האלה תוך כ-{c} חודשים.",
                           "An estimate: clear movement on these phrases in about {c} months.",
                           "Une estimation : un mouvement net sur ces expressions en environ {c} mois.",
                           "تقدير: تحرّك واضح في هذه العبارات خلال نحو {c} أشهر.",
                           "Оценка: заметное движение по этим фразам примерно за {c} мес."),
    "basis_judgment": _five("נפח ותחרות הם הערכה מקצועית (לא נתון נמדד).", "Volume and competition are a professional estimate (not measured data).",
                            "Volume et concurrence sont une estimation professionnelle (pas une donnée mesurée).",
                            "الحجم والمنافسة تقدير مهني (ليست بيانات مقاسة).", "Объём и конкуренция — профессиональная оценка (не измеренные данные)."),
}

INTENT_TEXT = {
    "informational": _five("מידע", "information", "information", "معلومات", "информация"),
    "commercial": _five("השוואה לפני קנייה", "comparing before buying", "comparaison avant achat", "مقارنة قبل الشراء", "сравнение перед покупкой"),
    "transactional": _five("רוצים לקנות / להזמין", "ready to buy / book", "prêt à acheter / réserver", "جاهز للشراء / الحجز", "готовы купить / записаться"),
    "local": _five("מחפשים בסביבה", "looking nearby", "cherche à proximité", "يبحث بالقرب", "ищут рядом"),
}
TIER_TEXT = {
    "high": _five("גבוה", "high", "élevé", "مرتفع", "высокий"),
    "medium": _five("בינוני", "medium", "moyen", "متوسط", "средний"),
    "low": _five("נמוך", "low", "faible", "منخفض", "низкий"),
}
CANDIDATE_LINE = _five("{k}: כוונה {i}, נפח {v}, תחרות {c}", "{k}: intent {i}, volume {v}, competition {c}",
                       "{k} : intention {i}, volume {v}, concurrence {c}", "{k}: النية {i}، الحجم {v}، المنافسة {c}",
                       "{k}: намерение {i}, объём {v}, конкуренция {c}")


def next_line(client_id: int, lang: str = "he") -> str:
    value = current(client_id)
    step = stage(value)
    return pick(TEXT["ready" if step == "ready" else f"next_{step}"], lang)


def candidate_lines(candidates: list, lang: str) -> str:
    rows = []
    for c in candidates:
        volume = f"~{c['monthly_searches']}" if c.get("monthly_searches") is not None else pick(TIER_TEXT.get(c.get("volume"), {}), lang) or "—"
        rows.append("• " + pick(CANDIDATE_LINE, lang).replace("{k}", c["keyword"])
                    .replace("{i}", pick(INTENT_TEXT.get(c.get("intent"), {}), lang) or "—")
                    .replace("{v}", volume).replace("{c}", pick(TIER_TEXT.get(c.get("competition"), {}), lang) or "—"))
    return "\n".join(rows)


_block_cache = {}


def prompt_block(client_id: int) -> dict:
    """`organic_plan` for the agents: where the sequence stands and what is next.
    Candidates are listed only after the research ran — never before."""
    import time
    hit = _block_cache.get(int(client_id))
    if hit and hit[0] > time.time() - 60:
        return hit[1]
    value = current(client_id)
    step = stage(value)
    out = {"stage": step, "next_step_text_he": pick(TEXT["ready" if step == "ready" else f"next_{step}"], "he"),
           "research_done": bool(value.get("research")),
           "approved_keywords": approved_keywords(client_id) if (value.get("approved") or {}) else [],
           "real_data_on": (value.get("research") or {}).get("basis") == "measured"}
    if value.get("research") and step == "approve":
        out["candidates"] = [{k: c.get(k) for k in ("keyword", "source", "intent", "volume", "competition", "monthly_searches")}
                             for c in value["research"]["candidates"]]
    if value.get("strategy"):
        s = value["strategy"]
        # the internal estimate stays out of every agent's data: an agent could repeat it
        out["strategy"] = {"steps": s["steps"], "committed_months": s["committed_months"], "is_estimate": True}
    _block_cache[int(client_id)] = (time.time(), out)
    return out
