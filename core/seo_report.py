"""The organic SEO report — a consultant-grade deliverable (2026-11-15,
docs/SEO-REPORT.md).

Reads like a proposal from a professional SEO consultant: where the site
stands, keyword opportunities, and BACKLINK OPPORTUNITIES matched to the
client's industry — per opportunity the site, the type, a quality score, why
it fits, what to do, and a ready outreach draft the client approves before
anything is sent. Nothing in this module sends anything.

THE REPORT IS FREE. It is part of the organic service: a client whose package
covers organic gets it; a client whose package does not is offered the
organic upgrade in ONE card (core/seo_report_actions) — never a one-off
payment for a report, and `build` refuses to run for a client who is not
covered (`covered()`), so no characterisation happens before a purchase or an
upgrade.

HARD RULES, enforced in code after the model (`clean`):
- never bought, sold or traded links, link schemes or PBNs — an opportunity
  whose text suggests one is DROPPED (it violates Google's guidelines and can
  harm the client);
- never a promised ranking — the report carries the fixed disclaimer and a
  line that promises one is removed;
- a site is listed only if the web search actually returned it (`sourced`);
  a model-invented site is dropped. Google Business Profile needs no site;
- SEO vendors are never named.

QUALITY SCORE (0-100): the model's judgment of relevance and content quality,
blended half-and-half with a public domain-authority number when
core/open_pagerank is enabled. Without the key the score is judgment only and
the report says so on every row.

Sources and cost: the site audit (free, the client's own site), the cached
market research (seo_agent.run_market_research, 7-day cache), ONE web-search
call for opportunities, ONE writing call. Costs are client_costs rows
(category seo, action `seo_report`).

Storage: app_settings `seo_report:<id>` (the latest report). No migration.
core/dataforseo_connector is NOT called (disabled; checked here).
"""
import html
import json
import re
from datetime import datetime, timezone

SERVICE_NAME = "seo_report"
PREFIX = "seo_report:"
LANGS = ("he", "en", "fr", "ar", "ru")
TYPES = ("directory", "unlinked_mention", "guest_post", "partnership", "google_business_profile")
ORGANIC_TOKENS = ("seo", "organic", "אורגני")
MAX_BACKLINKS, MAX_KEYWORDS, MAX_FINDINGS = 12, 10, 6
MIN_DAYS_BETWEEN = 7              # one fresh report a week is plenty; the stored one is shown meanwhile
VIEW_PATH = "/api/client/seo-report/view"

# Anything that smells of a paid link, a link scheme or a PBN drops the opportunity
FORBIDDEN = re.compile(
    r"(buy|bought|paid|pay for|purchas\w*|sponsor\w*|sell\w*)\W+(\w+\W+){0,3}(back)?links?"
    r"|link\s*(exchange|swap|scheme|farm|wheel|network)|reciprocal links?|\bPBNs?\b|private blog network"
    r"|קני(י)?ת קישורים|קישורים בתשלום|קישור(ים)? ממומנ|לקנות קישור|החלפת קישורים|חוות קישורים|רשת בלוגים פרטית"
    r"|liens? (payants?|sponsoris\w+)|achat de liens|échange de liens"
    r"|شراء (ال)?روابط|روابط مدفوعة|تبادل (ال)?روابط"
    r"|покупк\w+ ссыл\w+|платн\w+ ссыл\w+|обмен ссылк\w+", re.I)
# A line that promises a position is removed
PROMISE = re.compile(
    r"guarantee\w*|will rank|first page of google|#1 on google|top of google|page one"
    r"|מבטיח|מובטח|נבטיח|מקום ראשון בגוגל|עמוד ראשון בגוגל|להגיע לראש"
    r"|garanti\w*|première page|يضمن|مضمون|الصفحة الأولى|гарант\w+|перв\w+ страниц\w+", re.I)
VENDORS = re.compile(r"\b(semrush|ahrefs|dataforseo|open ?pagerank|domcop|moz(\.com)?|majestic|seoptimer|"
                     r"claude|anthropic|chatgpt|openai)\b", re.I)
_DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


TEXT = {
    "title": _five("דוח קידום אורגני", "Organic SEO report", "Rapport de référencement naturel",
                   "تقرير تحسين محركات البحث", "Отчёт по органическому продвижению"),
    "for": _five("עבור", "For", "Pour", "لـ", "Для"),
    "summary": _five("בשורה התחתונה", "The bottom line", "L'essentiel", "الخلاصة", "Главное"),
    "site_state": _five("איפה האתר עומד היום", "Where the site stands today", "Où en est le site aujourd'hui",
                        "أين يقف الموقع اليوم", "Текущее состояние сайта"),
    "keywords": _five("הזדמנויות במילות מפתח", "Keyword opportunities", "Opportunités de mots-clés",
                      "فرص الكلمات المفتاحية", "Возможности по ключевым словам"),
    "backlinks": _five("הזדמנויות לקישורים נכנסים", "Backlink opportunities", "Opportunités de liens entrants",
                       "فرص الروابط الواردة", "Возможности для входящих ссылок"),
    "next": _five("מה עושים עכשיו", "What to do next", "Prochaines étapes", "ماذا نفعل الآن", "Что делать дальше"),
    "col_site": _five("האתר", "Site", "Site", "الموقع", "Сайт"),
    "col_type": _five("סוג", "Type", "Type", "النوع", "Тип"),
    "col_score": _five("ציון איכות", "Quality score", "Score de qualité", "درجة الجودة", "Оценка качества"),
    "col_why": _five("למה זה מתאים", "Why it fits", "Pourquoi c'est pertinent", "لماذا يناسب", "Почему подходит"),
    "col_do": _five("מה עושים", "What to do", "Que faire", "ما المطلوب", "Что сделать"),
    "col_draft": _five("טיוטת פנייה", "Outreach draft", "Brouillon de message", "مسودة الرسالة", "Черновик письма"),
    "col_keyword": _five("ביטוי", "Keyword", "Mot-clé", "الكلمة", "Запрос"),
    "col_intent": _five("מה מחפשים", "What they are looking for", "Intention", "ما يبحثون عنه", "Что ищут"),
    "col_action": _five("מה לעשות איתו", "What to do with it", "Que faire", "ما العمل", "Что с ним делать"),
    "draft_note": _five(
        "הטיוטות כאן מוכנות לשליחה, אבל שום דבר לא נשלח. כל פנייה יוצאת רק אחרי שאישרתם אותה.",
        "The drafts here are ready to send, but nothing has been sent. Each message goes out only after you approve it.",
        "Les brouillons sont prêts, mais rien n'a été envoyé. Chaque message ne part qu'après votre validation.",
        "المسودات جاهزة للإرسال، لكن لم يُرسل شيء. كل رسالة تخرج فقط بعد موافقتكم عليها.",
        "Черновики готовы, но ничего не отправлено. Каждое письмо уходит только после вашего одобрения."),
    "score_both": _five(
        "הציון (0-100) משלב מדד ציבורי של סמכות הדומיין עם הערכה מקצועית שלנו של הרלוונטיות ואיכות התוכן.",
        "The score (0-100) combines a public measure of the domain's authority with our professional judgment of relevance and content quality.",
        "Le score (0-100) combine une mesure publique de l'autorité du domaine et notre jugement professionnel de la pertinence et de la qualité du contenu.",
        "الدرجة (0-100) تجمع مقياسًا عامًا لسلطة النطاق مع تقديرنا المهني للملاءمة وجودة المحتوى.",
        "Оценка (0-100) сочетает публичный показатель авторитетности домена и нашу профессиональную оценку релевантности и качества контента."),
    "score_judgment": _five(
        "הציון (0-100) הוא הערכה מקצועית בלבד של הרלוונטיות ואיכות התוכן. הוא לא כולל מדידה של סמכות הדומיין.",
        "The score (0-100) is a professional judgment only, of relevance and content quality. It does not include a measurement of the domain's authority.",
        "Le score (0-100) est uniquement un jugement professionnel de la pertinence et de la qualité du contenu. Il n'inclut pas de mesure de l'autorité du domaine.",
        "الدرجة (0-100) تقدير مهني فقط للملاءمة وجودة المحتوى. لا تتضمن قياسًا لسلطة النطاق.",
        "Оценка (0-100) — только профессиональное суждение о релевантности и качестве контента. Она не включает измерение авторитетности домена."),
    "judgment_tag": _five("הערכה בלבד", "judgment only", "jugement seul", "تقدير فقط", "только оценка"),
    "no_promise": _five(
        "קידום אורגני הוא עבודה מצטברת. אף אחד לא יכול להבטיח מיקום בגוגל, וגם אנחנו לא מבטיחים. הדוח מציג הזדמנויות, לא תוצאות.",
        "Organic SEO is cumulative work. No one can promise a position on Google, and neither do we. This report shows opportunities, not results.",
        "Le référencement naturel est un travail cumulatif. Personne ne peut promettre une position sur Google, et nous non plus. Ce rapport présente des opportunités, pas des résultats.",
        "تحسين محركات البحث عمل تراكمي. لا أحد يستطيع أن يعد بترتيب في غوغل، ولا نحن. يعرض التقرير فرصًا لا نتائج.",
        "Органическое продвижение — накопительная работа. Никто не может обещать позицию в Google, и мы тоже. Отчёт показывает возможности, а не результаты."),
    "clean_links": _five(
        "כל ההזדמנויות כאן הן קישורים שמרוויחים ביושר. אנחנו לא קונים, לא מוכרים ולא מחליפים קישורים: זה נוגד את ההנחיות של גוגל ועלול לפגוע באתר.",
        "Every opportunity here is a link you earn honestly. We never buy, sell or trade links: it violates Google's guidelines and can harm the site.",
        "Chaque opportunité ici est un lien gagné honnêtement. Nous n'achetons, ne vendons ni n'échangeons jamais de liens : c'est contraire aux consignes de Google et peut nuire au site.",
        "كل فرصة هنا رابط يُكتسب بنزاهة. لا نشتري ولا نبيع ولا نتبادل الروابط: ذلك يخالف إرشادات غوغل وقد يضر بالموقع.",
        "Каждая возможность здесь — честно заработанная ссылка. Мы не покупаем, не продаём и не обмениваем ссылки: это нарушает правила Google и может навредить сайту."),
    "none_found": _five(
        "בחיפוש הפעם לא מצאנו אתרים שאפשר לעמוד מאחוריהם, אז לא רשמנו אף אחד. עדיף רשימה ריקה מרשימה מומצאת.",
        "This search did not turn up sites we can stand behind, so we listed none. An empty list is better than an invented one.",
        "Cette recherche n'a pas fait ressortir de sites fiables : nous n'en avons listé aucun. Mieux vaut une liste vide qu'une liste inventée.",
        "لم يُظهر البحث هذه المرة مواقع يمكن الوثوق بها، لذا لم ندرج أيًا منها. قائمة فارغة أفضل من قائمة مختلقة.",
        "В этот раз поиск не нашёл сайтов, за которые мы можем ручаться, поэтому список пуст. Пустой список лучше выдуманного."),
    "print": _five("כדי לשמור כ-PDF: הדפסה ← שמירה כ-PDF.", "To save as PDF: Print → Save as PDF.",
                   "Pour enregistrer en PDF : Imprimer → Enregistrer en PDF.", "للحفظ بصيغة PDF: طباعة ← حفظ كـ PDF.",
                   "Чтобы сохранить PDF: Печать → Сохранить как PDF."),
}
TYPE_TEXT = {
    "directory": _five("אינדקס או פורטל", "Directory or portal", "Annuaire ou portail", "دليل أو بوابة", "Каталог или портал"),
    "unlinked_mention": _five("אזכור בלי קישור", "Mention without a link", "Mention sans lien", "ذكر بلا رابط",
                              "Упоминание без ссылки"),
    "guest_post": _five("כתבת אורח", "Guest post", "Article invité", "مقال ضيف", "Гостевая статья"),
    "partnership": _five("שיתוף פעולה או פודקאסט", "Partnership or podcast", "Partenariat ou podcast", "شراكة أو بودكاست",
                         "Партнёрство или подкаст"),
    "google_business_profile": _five("פרופיל העסק בגוגל", "Google Business Profile", "Fiche d'établissement Google",
                                     "ملف النشاط التجاري على Google", "Профиль компании в Google"),
}

# The SMALL TASTE a client without the organic service gets (2026-11-16): the
# KINDS of places we would approach, said in code — never a real site's name,
# never the opportunity list, never an outreach draft. The full report stays
# with the service.
TASTE_KIND = {
    "directory": _five("אינדקסים ופורטלים של התחום והאזור, שבהם לקוחות מחפשים עסק כמו שלכם",
                       "Industry and local directories where customers look for a business like yours",
                       "Des annuaires du secteur et locaux où les clients cherchent une entreprise comme la vôtre",
                       "أدلة القطاع والأدلة المحلية التي يبحث فيها العملاء عن نشاط مثل نشاطكم",
                       "Отраслевые и местные каталоги, где клиенты ищут бизнес вроде вашего"),
    "unlinked_mention": _five("מקומות שכבר מזכירים את העסק שלכם בלי קישור לאתר",
                              "Places that already mention your business without a link to your site",
                              "Des pages qui citent déjà votre entreprise sans lien vers votre site",
                              "أماكن تذكر نشاطكم بالفعل دون رابط إلى موقعكم",
                              "Места, где ваш бизнес уже упоминают без ссылки на сайт"),
    "guest_post": _five("אתרים בתחום שלכם שמפרסמים כתבות של אנשי מקצוע",
                        "Sites in your field that publish articles by professionals",
                        "Des sites de votre secteur qui publient des articles de professionnels",
                        "مواقع في مجالكم تنشر مقالات لأصحاب الاختصاص",
                        "Сайты вашей сферы, которые публикуют статьи специалистов"),
    "partnership": _five("עסקים משלימים, פודקאסטים וקהילות שישמחו לשיתוף פעולה",
                         "Neighbouring businesses, podcasts and communities that would welcome a collaboration",
                         "Des entreprises complémentaires, des podcasts et des communautés ouverts à une collaboration",
                         "أنشطة مكمّلة وبودكاستات ومجتمعات ترحّب بالتعاون",
                         "Смежные бизнесы, подкасты и сообщества, готовые к сотрудничеству"),
    "google_business_profile": _five("פרופיל העסק שלכם בגוגל, מלא ומעודכן",
                                     "Your business profile on Google, complete and up to date",
                                     "Votre fiche d'établissement Google, complète et à jour",
                                     "ملف نشاطكم التجاري على Google، مكتمل ومحدّث",
                                     "Профиль вашей компании в Google — полный и актуальный"),
}
TASTE_SHOWN = ("directory", "partnership", "google_business_profile")
TASTE_INTRO = _five("טעימה קטנה ממה שהייתי מחפש בשביל העסק שלכם:",
                    "A small taste of what I'd look for for your business:",
                    "Un petit aperçu de ce que je chercherais pour votre entreprise :",
                    "لمحة صغيرة عمّا كنت سأبحث عنه لنشاطكم:",
                    "Небольшой пример того, что я бы искал для вашего бизнеса:")
TASTE_OUTRO = _five("את ההזדמנויות עצמן, עם טיוטות פנייה מוכנות, מקבלים בדוח המלא, שהוא חלק משירות הקידום האורגני.",
                    "The opportunities themselves, with ready outreach drafts, come in the full report, which is part of the organic service.",
                    "Les opportunités elles-mêmes, avec des brouillons de messages, figurent dans le rapport complet, inclus dans le service de référencement naturel.",
                    "الفرص نفسها مع مسودات رسائل جاهزة تأتي في التقرير الكامل، وهو جزء من خدمة التحسين المجاني.",
                    "Сами возможности с готовыми черновиками писем — в полном отчёте, он входит в услугу органического продвижения.")


def taste(lang: str) -> str:
    """The small taste, code-built: three KINDS of places, no names, no drafts."""
    lang = lang if lang in LANGS else "en"
    return "\n".join([TASTE_INTRO[lang]] + [f"• {TASTE_KIND[k][lang]}" for k in TASTE_SHOWN] + [TASTE_OUTRO[lang]])


def t(key: str, lang: str) -> str:
    table = TEXT[key]
    return table.get(lang) or table["en"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _db():
    from agents.client_agent import _db as db
    return db()


# ─── Who gets it ─────────────────────────────────────────────────────────────

def covered(services) -> bool:
    """The purchased package covers organic. The ONE rule for the report."""
    return any(any(token in str(service).strip().lower() for token in ORGANIC_TOKENS) for service in services or [])


def coverage(client_id: int) -> dict:
    """{"readable", "covered"} from the client's purchased package. A package
    that cannot be read is NOT covered (fail closed) and is said to be unreadable."""
    try:
        from core.client_journey import _chosen_package
        package = _chosen_package(client_id) or {}
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: package unreadable ({type(e).__name__})")
        return {"readable": False, "covered": False}
    services = package.get("recommended_services") or []
    return {"readable": bool(package), "covered": covered(services)}


# ─── Storage ─────────────────────────────────────────────────────────────────

def latest(client_id: int) -> dict:
    rows = (_db().table("app_settings").select("value").eq("key", f"{PREFIX}{int(client_id)}")
            .limit(1).execute().data or [])
    value = rows[0].get("value") if rows else None
    return value if isinstance(value, dict) and value.get("report") else {}


def _store(client_id: int, value: dict):
    _db().table("app_settings").upsert({"key": f"{PREFIX}{int(client_id)}", "value": value,
                                        "updated_at": _now().isoformat()}, on_conflict="key").execute()
    _block_cache.pop(int(client_id), None)


def status(client_id: int) -> dict:
    """What the agents and the dashboard read; never raises."""
    try:
        value = latest(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: stored report unreadable ({type(e).__name__})")
        return {"available": False}
    cover = coverage(client_id)
    return {"available": True, "package_readable": cover["readable"], "covered_by_package": cover["covered"],
            "has_report": bool(value), "built_at": value.get("built_at", ""),
            "opportunities": len((value.get("report") or {}).get("backlink_opportunities") or []),
            "view_url": VIEW_PATH if value else ""}


_block_cache = {}   # client_id -> (at, status) — a chat turn must not wait on these reads
BLOCK_CACHE_SECONDS = 60


def prompt_block(client_id: int) -> dict:
    """`seo_report` in every agent's payload ({} when unreadable)."""
    import time
    hit = _block_cache.get(int(client_id))
    if hit and hit[0] > time.time() - BLOCK_CACHE_SECONDS:
        return hit[1]
    data = status(client_id)
    if not data.get("available") or not data.get("package_readable"):
        return {}
    out = {"covered_by_package": data["covered_by_package"], "has_report": data["has_report"],
           "built_at": data["built_at"][:10], "is_free_part_of_the_organic_service": True}
    if not data["covered_by_package"]:
        # The SMALL TASTE (STUDIO_RULE): kinds only — the model picks the ones that fit
        out["taste_kinds"] = [TASTE_KIND[k]["en"] for k in TYPES]
    _block_cache[int(client_id)] = (time.time(), out)
    return out


# ─── Cleaning what the model wrote ───────────────────────────────────────────

def _line(value, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = VENDORS.sub("", text)
    return re.sub(r"\s{2,}", " ", text).strip()[:limit]


def _keep(text: str) -> str:
    """"" for a line that promises a ranking — it is removed, not softened."""
    return "" if PROMISE.search(text or "") else text


def domain_of(value: str) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"^[a-z]+://", "", value).split("/")[0].split("?")[0].split(":")[0]
    value = value[4:] if value.startswith("www.") else value
    return value if _DOMAIN.match(value) else ""


def _grade(value) -> int:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 3


def score_of(relevance: int, quality: int, authority=None) -> int:
    """0-100. Judgment alone, or half judgment and half the public authority
    number (0-10) when one was measured."""
    judgment = (relevance + quality) / 10 * 100
    if authority is None:
        return int(round(judgment))
    return int(round(0.5 * judgment + 0.5 * float(authority) * 10))


def clean(raw: dict, sourced: list, own_domain: str = "", authority: dict = None) -> dict:
    """The model's draft → the report we stand behind (see the module rules)."""
    raw = raw if isinstance(raw, dict) else {}
    authority = authority or {}
    allowed = {domain_of(u) for u in sourced or []} - {""}
    findings = [f for f in (_keep(_line(x, 240)) for x in (raw.get("site_state") or [])[:MAX_FINDINGS * 2]) if f]
    keywords = []
    for item in (raw.get("keyword_opportunities") or []):
        if not isinstance(item, dict):
            continue
        keyword = _line(item.get("keyword"), 80)
        if not keyword:
            continue
        keywords.append({"keyword": keyword, "intent": _keep(_line(item.get("intent"), 160)),
                         "action": _keep(_line(item.get("action"), 220))})
    backlinks, dropped = [], {"paid_or_scheme": 0, "not_from_search": 0, "duplicate": 0}
    seen = set()
    for item in (raw.get("backlink_opportunities") or []):
        if not isinstance(item, dict):
            continue
        kind = item.get("type") if item.get("type") in TYPES else ""
        if not kind:
            continue
        site = domain_of(item.get("site") or item.get("url"))
        fields = {"why": _line(item.get("why_it_fits"), 260), "do": _line(item.get("what_to_do"), 300),
                  "draft": re.sub(r"[ \t]+", " ", VENDORS.sub("", str(item.get("outreach_draft") or ""))).strip()[:900]}
        if FORBIDDEN.search(" ".join(fields.values())):
            dropped["paid_or_scheme"] += 1
            continue
        if kind == "google_business_profile":
            site, fields["draft"] = "", ""             # their own profile: nothing to send to anyone
        elif not site or site == own_domain or site not in allowed:
            dropped["not_from_search"] += 1            # never a site the search did not return
            continue
        key = (kind, site)
        if key in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(key)
        relevance, quality = _grade(item.get("relevance")), _grade(item.get("content_quality"))
        measured = authority.get(site) if site else None
        backlinks.append({"site": site, "type": kind, "why_it_fits": _keep(fields["why"]),
                          "what_to_do": _keep(fields["do"]), "outreach_draft": _keep(fields["draft"]),
                          "relevance": relevance, "content_quality": quality,
                          "authority": measured, "score": score_of(relevance, quality, measured),
                          "score_basis": "authority_and_judgment" if measured is not None else "judgment_only"})
    backlinks.sort(key=lambda b: -b["score"])
    return {"summary": _keep(_line(raw.get("summary"), 500)),
            "site_state": findings[:MAX_FINDINGS], "keyword_opportunities": keywords[:MAX_KEYWORDS],
            "backlink_opportunities": backlinks[:MAX_BACKLINKS],
            "next_steps": [s for s in (_keep(_line(x, 220)) for x in (raw.get("next_steps") or [])[:8]) if s][:5],
            "dropped": dropped}


# ─── Building it ─────────────────────────────────────────────────────────────

SEARCH_SYSTEM = f"""You are a senior SEO consultant researching BACKLINK OPPORTUNITIES for ONE small Israeli business.
Search the web and find real sites, in the Israeli market, where this business could EARN a link:
1. industry and local Israeli directories and portals the business is not listed in yet;
2. existing mentions of the business that carry no link;
3. sites in their field that accept guest posts or expert articles;
4. partnerships: complementary businesses, associations, local bodies, and podcasts in the field.

NEVER buy, sell, trade or suggest paid links, sponsored posts for links, link exchanges, link schemes or
PBNs — they violate Google's guidelines and can harm the client. Earned links only.
Never invent a site: list only sites your searches actually returned. Never state metrics you did not read.

Run at most 6 focused searches. Output PLAIN TEXT, max 30 short lines, one site per line:
TYPE (directory | unlinked_mention | guest_post | partnership) | full URL | what the site is | why it fits
this business. Up to {MAX_BACKLINKS} sites in total. If you found fewer good ones, list fewer."""

REPORT_SYSTEM = f"""You are a senior SEO consultant writing an organic SEO report for ONE small Israeli business — it must
read like a proposal from a professional consultant: specific to THIS business, plain words, no filler.
You get: the business, an audit of their own site (real data), market research, the consultant's web
research of backlink opportunities, and the list of URLs the web search actually returned.

Write every text in the language named in "language".

HARD RULES:
- NEVER suggest buying, selling or trading links, sponsored links, link exchanges, link schemes or PBNs.
  Earned links only.
- NEVER promise or predict a ranking, a position or a date. No "guarantee".
- Backlink opportunities: ONLY sites whose domain appears in "urls_from_search". Never add one from
  memory. The one exception is type "google_business_profile" (their own profile on Google — include it
  once, with what to complete or improve there; it has no site and no outreach draft).
- Never invent numbers: no search volumes, no traffic, no authority metrics. Use only numbers present in
  the data you were given.
- Never name an SEO tool or vendor.

LIMITS: summary ≤ 70 words. site_state: up to {MAX_FINDINGS} findings, each ≤ 35 words, each grounded in the
audit data. keyword_opportunities: up to {MAX_KEYWORDS}; intent ≤ 20 words; action ≤ 30 words.
backlink_opportunities: up to {MAX_BACKLINKS}; why_it_fits ≤ 35 words; what_to_do ≤ 40 words (the exact next
action); outreach_draft ≤ 90 words, a ready message from the business owner to that site, polite,
specific, signed with the business name — a draft the owner approves before anything is sent.
relevance and content_quality: integers 1-5, your honest judgment (5 = squarely in their field /
well-edited, real content). next_steps: up to 5, each ≤ 30 words, ordered by what to do first.

Return JSON only:
{{"summary": "", "site_state": [""], "keyword_opportunities": [{{"keyword": "", "intent": "", "action": ""}}],
"backlink_opportunities": [{{"site": "domain.co.il", "type": "directory|unlinked_mention|guest_post|partnership|google_business_profile",
"why_it_fits": "", "what_to_do": "", "outreach_draft": "", "relevance": 3, "content_quality": 3}}],
"next_steps": [""]}}"""


class ReportError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def _audit_summary(audit: dict) -> dict:
    keys = ("connected", "site_url", "posts_total", "pages_total", "published_posts", "days_since_last_post",
            "thin_content", "missing_excerpt", "stale_content")
    out = {k: audit.get(k) for k in keys if audit.get(k) not in (None, "", [])}
    out["page_titles"] = [i.get("title") for i in (audit.get("inventory") or []) if i.get("status") == "publish"][:25]
    return out


def _sources(client_id: int, focus: str) -> dict:
    """Everything the report is written from. Each piece is its own seam."""
    from agents import seo_agent
    domain = seo_agent._client_domain(client_id)
    business = seo_agent._business_context(client_id)
    answers = business.get("sales_chat_answers") or {}
    business["sales_chat_answers"] = {k: v for k, v in answers.items() if "budget" not in str(k).lower()}
    audit = seo_agent.audit_site(client_id) if domain else {"connected": False}
    research = seo_agent.run_market_research(client_id) if domain else {}
    return {"domain": domain, "business": business, "audit": audit, "research": research,
            "focus": re.sub(r"\s+", " ", str(focus or "")).strip()[:300]}


def build(client_id: int, focus: str = "", lang: str = "he") -> dict:
    """Builds, cleans and stores the report. Raises ReportError("not_covered")
    for a client whose package does not cover organic — BEFORE any research."""
    from core import claude_json, dataforseo_connector, open_pagerank
    from core.cost_tracker import cost_action
    from core.model_routing import TIER_MODELS
    cover = coverage(client_id)
    if not cover["covered"]:
        raise ReportError("not_covered" if cover["readable"] else "package_unreadable")
    lang = lang if lang in LANGS else "he"
    with cost_action("seo_report", f"seo-report-{int(client_id)}"), claude_json.collect_usage() as used:
        data = _sources(client_id, focus)
        if not data["domain"]:
            raise ReportError("no_site")
        payload = {"business": data["business"], "domain": data["domain"], "focus": data["focus"]}
        try:
            found, sourced = claude_json.claude_web_search_call(
                SEARCH_SYSTEM, json.dumps(payload, ensure_ascii=False), max_tokens=2200, client_id=client_id,
                cost_category="claude_seo_research", with_sources=True)
        except Exception as e:
            # No search = no sites we can stand behind. The report is still
            # written (site state, keywords, the Google profile), with no site list.
            print(f"[{SERVICE_NAME}] client {client_id}: opportunity search failed ({type(e).__name__})")
            found, sourced = "", []
        research = data["research"] if isinstance(data["research"], dict) else {}
        writer_input = {"language": lang, **payload, "site_audit": _audit_summary(data["audit"]),
                        "market_research": {k: research.get(k) for k in ("source", "summary", "data") if research.get(k)},
                        "backlink_research": found, "urls_from_search": sorted({domain_of(u) for u in sourced} - {""})}
        try:
            raw = claude_json.safe_claude_json_call(
                REPORT_SYSTEM, json.dumps(writer_input, ensure_ascii=False, default=str), max_tokens=7000,
                model=TIER_MODELS["heavy"], client_id=client_id, cost_category="claude_seo_report")
        except Exception as e:
            raise ReportError("writing_failed", f"{type(e).__name__}: {e}")
        # Only the sites that SURVIVED cleaning are looked up: third-party domains the search
        # returned — never the client's own domain, never a dropped or invented one.
        report = clean(raw, sourced, data["domain"])
        domains = [b["site"] for b in report["backlink_opportunities"] if b["site"]]
        # dataforseo_connector is disabled and never called here (its ENABLED is False)
        authority = open_pagerank.scores(domains) if not dataforseo_connector.ENABLED else {}
        if authority:
            report = clean(raw, sourced, data["domain"], authority)
    if not (report["site_state"] or report["keyword_opportunities"] or report["backlink_opportunities"]):
        raise ReportError("writing_failed", "empty report")
    value = {"report": report, "built_at": _now().isoformat(), "language": lang, "domain": data["domain"],
             "business_name": (data["business"] or {}).get("name", ""), "focus": data["focus"],
             "score_basis": "authority_and_judgment" if authority else "judgment_only",
             "cost_ils": round(sum(float(u.get("ils") or 0) for u in used), 3)}
    _store(client_id, value)
    return value


def fresh_enough(value: dict) -> bool:
    try:
        built = datetime.fromisoformat(str(value.get("built_at")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return (_now() - built).days < MIN_DAYS_BETWEEN


# ─── Reading it: the page and the export ─────────────────────────────────────

def _e(value) -> str:
    return html.escape(str(value or ""), quote=True)


def page_headers() -> dict:
    csp = "; ".join(["sandbox", "default-src 'none'", "style-src 'unsafe-inline' https://fonts.googleapis.com",
                     "font-src https://fonts.gstatic.com", "img-src data:", "base-uri 'none'", "form-action 'none'",
                     "object-src 'none'", "frame-ancestors 'none'"])
    return {"Cache-Control": "no-store", "Content-Security-Policy": csp, "X-Content-Type-Options": "nosniff",
            "X-Robots-Tag": "noindex", "Referrer-Policy": "no-referrer"}


def _score_cell(item: dict, lang: str) -> str:
    tag = "" if item.get("score_basis") == "authority_and_judgment" else f' <small>({_e(t("judgment_tag", lang))})</small>'
    return f'<b>{int(item.get("score") or 0)}</b>{tag}'


def page_html(value: dict) -> str:
    """The report as one printable page, in the language it was written in."""
    report = value.get("report") or {}
    lang = value.get("language") if value.get("language") in LANGS else "he"
    direction = "rtl" if lang in ("he", "ar") else "ltr"
    date = str(value.get("built_at") or "")[:10]
    parts = []
    if report.get("summary"):
        parts.append(f'<h2>{_e(t("summary", lang))}</h2><p class="lead">{_e(report["summary"])}</p>')
    if report.get("site_state"):
        parts.append(f'<h2>{_e(t("site_state", lang))}</h2><ul>'
                     + "".join(f"<li>{_e(x)}</li>" for x in report["site_state"]) + "</ul>")
    if report.get("keyword_opportunities"):
        rows = "".join(f'<tr><td><b>{_e(k["keyword"])}</b></td><td>{_e(k.get("intent"))}</td>'
                       f'<td>{_e(k.get("action"))}</td></tr>' for k in report["keyword_opportunities"])
        parts.append(f'<h2>{_e(t("keywords", lang))}</h2><table><thead><tr><th>{_e(t("col_keyword", lang))}</th>'
                     f'<th>{_e(t("col_intent", lang))}</th><th>{_e(t("col_action", lang))}</th></tr></thead>'
                     f'<tbody>{rows}</tbody></table>')
    links = report.get("backlink_opportunities") or []
    basis = "score_both" if value.get("score_basis") == "authority_and_judgment" else "score_judgment"
    parts.append(f'<h2>{_e(t("backlinks", lang))}</h2><p class="note">{_e(t("clean_links", lang))}</p>')
    if links:
        cards = ""
        for item in links:
            site = (f'<span class="site" dir="ltr">{_e(item["site"])}</span>' if item.get("site")
                    else f'<span class="site">{_e(t("col_site", lang))}: Google</span>')
            draft = (f'<details><summary>{_e(t("col_draft", lang))}</summary><pre>{_e(item["outreach_draft"])}</pre></details>'
                     if item.get("outreach_draft") else "")
            cards += (f'<div class="opp"><div class="opp-head">{site}'
                      f'<span class="type">{_e(TYPE_TEXT[item["type"]].get(lang) or TYPE_TEXT[item["type"]]["en"])}</span>'
                      f'<span class="score">{_e(t("col_score", lang))}: {_score_cell(item, lang)}</span></div>'
                      f'<p><b>{_e(t("col_why", lang))}:</b> {_e(item.get("why_it_fits"))}</p>'
                      f'<p><b>{_e(t("col_do", lang))}:</b> {_e(item.get("what_to_do"))}</p>{draft}</div>')
        parts.append(f'<p class="note">{_e(t(basis, lang))}</p>{cards}<p class="note">{_e(t("draft_note", lang))}</p>')
    else:
        parts.append(f'<p>{_e(t("none_found", lang))}</p>')
    if report.get("next_steps"):
        parts.append(f'<h2>{_e(t("next", lang))}</h2><ol>'
                     + "".join(f"<li>{_e(x)}</li>" for x in report["next_steps"]) + "</ol>")
    name = value.get("business_name") or value.get("domain") or ""
    return f"""<!doctype html>
<html lang="{lang}" dir="{direction}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>{_e(t("title", lang))}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Heebo:wght@400;700;900&amp;family=Rubik:wght@400;700&amp;family=Cairo:wght@400;700&amp;display=swap">
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#F5F4F0;color:#1B1B1F;font-family:'Heebo','Rubik','Cairo',sans-serif;line-height:1.6}}
main{{max-width:860px;margin:0 auto;padding:28px 16px 60px;background:#fff}}
h1{{font-size:28px;margin:0;font-weight:900}} .sub{{color:#555;margin:2px 0 18px;font-size:15px}}
h2{{font-size:20px;margin:30px 0 8px;padding-bottom:6px;border-bottom:3px solid #FF4C1F;display:inline-block}}
.lead{{font-size:17px}} .note{{font-size:14px;color:#44444A;background:#F5F4F0;border-radius:10px;padding:10px 12px}}
table{{width:100%;border-collapse:collapse;font-size:14.5px}} th,td{{text-align:start;vertical-align:top;padding:8px;border-bottom:1px solid #E4E2DC}}
th{{font-size:13px;color:#555}}
.opp{{border:1px solid #E4E2DC;border-radius:12px;padding:12px 14px;margin:12px 0;break-inside:avoid}}
.opp-head{{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:baseline;margin-bottom:4px}}
.site{{font-weight:900;font-size:17px;overflow-wrap:anywhere}} .type{{font-size:13px;background:#1B1B1F;color:#fff;border-radius:99px;padding:1px 10px}}
.score{{font-size:14px;margin-inline-start:auto}} .opp p{{margin:6px 0;font-size:15px}}
details{{margin-top:6px}} summary{{cursor:pointer;font-weight:700;font-size:14.5px}}
pre{{white-space:pre-wrap;font-family:inherit;font-size:14.5px;background:#F5F4F0;border-radius:10px;padding:10px 12px;margin:6px 0 0}}
.print{{font-size:13px;color:#777;margin-top:34px}}
@media print{{body{{background:#fff}} main{{padding:0}} details{{display:block}} details>summary{{list-style:none}} .print{{display:none}}}}
</style></head><body><main>
<h1>{_e(t("title", lang))}</h1>
<p class="sub">{_e(t("for", lang))} {_e(name)} · <span dir="ltr">{_e(value.get("domain"))}</span> · {_e(date)}</p>
{"".join(parts)}
<p class="note">{_e(t("no_promise", lang))}</p>
<p class="print">{_e(t("print", lang))}</p>
</main></body></html>"""


def export_table(value: dict) -> dict:
    """{title, subtitle, columns, rows, summary, rtl} — the shape
    core/export_service's writers take (docx / xlsx / csv)."""
    report = value.get("report") or {}
    lang = value.get("language") if value.get("language") in LANGS else "he"
    columns = [t(k, lang) for k in ("col_site", "col_type", "col_score", "col_why", "col_do", "col_draft")]
    rows = [[i.get("site") or "Google", TYPE_TEXT[i["type"]].get(lang) or TYPE_TEXT[i["type"]]["en"],
             f'{i.get("score")}' + ("" if i.get("score_basis") == "authority_and_judgment"
                                    else f' ({t("judgment_tag", lang)})'),
             i.get("why_it_fits", ""), i.get("what_to_do", ""), i.get("outreach_draft", "")]
            for i in report.get("backlink_opportunities") or []]
    summary = ([report.get("summary", "")] if report.get("summary") else []) \
        + [f'{t("site_state", lang)}: {x}' for x in report.get("site_state") or []] \
        + [f'{t("keywords", lang)}: {k["keyword"]} — {k.get("action", "")}' for k in report.get("keyword_opportunities") or []] \
        + [f'{t("next", lang)}: {x}' for x in report.get("next_steps") or []] \
        + [t("clean_links", lang), t("no_promise", lang), t("draft_note", lang)]
    return {"title": t("title", lang), "subtitle": f'{value.get("business_name") or ""} · {value.get("domain") or ""} · '
                                                   f'{str(value.get("built_at") or "")[:10]}',
            "columns": columns, "rows": rows, "summary": summary, "rtl": lang in ("he", "ar")}
