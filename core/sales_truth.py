"""No invented number before purchase (2026-11-28).

The sales chat was the one place still writing numbers with nothing behind
them: 90-day result targets and an "average cost per lead in the industry"
that the model produced from its own general knowledge. The system holds NO
industry benchmark data, so no such figure can be said — and a prospect who
buys on a number we made up is a client who leaves in two months.

This module is the last thing a proposal (and a closer's reply) passes
through before a prospect reads it. It does not ask the model to behave; it
removes what the model may not produce:

- `market_reality` and `kpis` are deleted from every proposal. The fields no
  longer exist in what a prospect, an email or an agent is given.
- every remaining number must have a BASIS, the same required field as
  core/campaign_presentation: {"kind": ...}. Before purchase only two kinds
  exist: `package` (our own price list and what the package contains) and
  `prospect` (a figure the prospect gave us, said as theirs). A sentence with
  a number that has neither is removed. So is a sentence that states a market
  norm in words ("typically in this field").
- the 90-day list is WHAT WE DO: an item that pairs a number with a result
  (enquiries, sales, followers, rankings) is removed whoever the number
  belongs to.
- what CAN be said is written here, not by the model: how the first period
  works (a learning period, as core/target_inputs states for a client), what
  we measure, and the prospect's own figures marked as theirs.

`figures` on the proposal records every number that was kept and its basis.
"""
import re

LANGS = ("he", "en", "fr", "ar", "ru")
REMOVED_FIELDS = ("market_reality", "kpis")
KINDS = ("package", "prospect")
LEARNING_DAYS = 30            # = core/target_inputs.LEARNING_DAYS
# Counts that are facts of the service itself (months, weekly rhythm, the fixed terms), never a result.
PLAIN = {str(n) for n in (*range(1, 13), 15, 20, 24, 30, 80, 90, 100, 2026, 2027)}

NUMBER = re.compile(r"\d{1,3}(?:[,   ]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
SENTENCE = re.compile(r"(?<=[.!?؟])\s+|\n+")

# A RESULT: something that happens in the market, not something we do.
OUTCOME = re.compile(
    r"ליד|פניות|פנייה|מכירות|לקוחות חדשים|עוקבים|המרות|הכנסות|החזר|קליקים|חשיפות|צפיות|עמוד ראשון|עמוד הראשון|מקום ראשון|דירוג|"
    r"תנועה|מבקרים|"
    r"\bleads?\b|enquir|inquir|\bsales\b|new (?:customers|clients)|followers|conversions?|revenue|\broi\b|return on|clicks|"
    r"impressions|\bviews\b|ranking|traffic|visitors|page one|first page|"
    r"prospects|demandes|ventes|nouveaux clients|abonnés|chiffre d'affaires|clics|vues|classement|trafic|visiteurs|première page|"
    r"عملاء جدد|عملاء محتمل|استفسار|مبيعات|متابع|تحويل|إيراد|نقرات|مشاهدات|ترتيب|زيارات|زوار|الصفحة الأولى|"
    r"лид|заявк|обращени|продаж|новых клиент|подписчик|конверси|выручк|доход|клик|показ|просмотр|позици|трафик|посетител|первой страниц",
    re.IGNORECASE)

# A market norm said in words. There is no stored benchmark, so there is nothing to say.
BENCHMARK = re.compile(
    r"ממוצע\S*\s+(?:\S+\s+){0,3}(?:בענף|בתחום|בשוק)|(?:בענף|בתחום|בשוק)\s+(?:\S+\s+){0,3}ממוצע|בדרך כלל ב(?:תחום|ענף|שוק)|"
    r"מקובל ב(?:תחום|ענף|שוק)|נתוני שוק|עלות ליד|עלות לליד|עלות קליק|עלות לקליק|"
    r"industry average|market average|average (?:cost|cpl|cpc)|typical(?:ly)? (?:in|for) (?:this|your|the) (?:field|industry|market|niche)|"
    r"benchmark|cost[- ]per[- ](?:lead|click)|market data|"
    r"moyenne du (?:secteur|marché)|coût par (?:lead|prospect|clic)|en général dans (?:ce|votre) secteur|données du marché|"
    r"متوسط (?:القطاع|السوق|المجال)|تكلفة العميل المحتمل|تكلفة النقرة|بيانات السوق|عادةً في (?:هذا|مجال)|"
    r"в среднем по (?:отрасли|рынку|нише)|стоимость (?:лида|клика|заявки)|обычно в (?:этой|вашей) (?:сфере|нише|отрасли)|рыночные данные",
    re.IGNORECASE)

# "This figure is yours": required when a prospect's own number is said next to a result.
THEIRS = re.compile(
    r"מסרת|סיפרת|אמרת|ציינת|כתבת|לדברי|שביקשת|שהגדרת|שלך\b|שלכם|"
    r"you (?:told|said|mentioned|gave|wrote|stated|asked)|\byour\b|"
    r"vous (?:avez|nous avez)|\bvotre\b|\bvos\b|"
    r"ذكرت|قلت|أخبرت|كتبت|حسب قول|ـكم\b|لديكم|"
    r"вы (?:сказали|указали|написали|назвали|сообщили)|\bваш|по вашим", re.IGNORECASE)


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


T = {
    "first_title": _five("איך עובדת התקופה הראשונה", "How the first period works", "Comment se passe la première période",
                         "كيف تسير الفترة الأولى", "Как проходит первый период"),
    "first_text": _five(
        "30 הימים הראשונים הם תקופת למידה. בזמן הזה אנחנו מקימים, מחברים את המדידה ומתחילים לפעול, ובסופם יש נקודת התחלה "
        "שנמדדה אצלכם. רק עליה נקבע יחד יעד מספרי. לפני כן לא ניתן לכם מספר של תוצאות: אין לנו עדיין נתונים שלכם לבסס אותו "
        "עליהם, ואיננו משתמשים בממוצעים כלליים. אם אחרי 30 יום עדיין אין מספיק נתונים, נגיד את זה ולא נמציא מספר.",
        "The first 30 days are a learning period. In that time we set things up, connect the measurement and start working, and at "
        "the end there is a starting point measured in your own business. Only on that do we set a numeric target together. Before "
        "then we will not give you a results number: we have none of your own data yet to base it on, and we do not use general "
        "averages. If after 30 days there is still too little data, we will say so and will not make a number up.",
        "Les 30 premiers jours sont une période d'apprentissage. Pendant ce temps, nous mettons tout en place, connectons la mesure "
        "et commençons à travailler ; à la fin, il existe un point de départ mesuré chez vous. C'est seulement sur cette base que "
        "nous fixons ensemble un objectif chiffré. Avant cela, nous ne vous donnerons aucun chiffre de résultat : nous n'avons pas "
        "encore vos propres données pour l'appuyer, et nous n'utilisons pas de moyennes générales. Si après 30 jours les données "
        "restent insuffisantes, nous le dirons sans inventer de chiffre.",
        "أول 30 يومًا هي فترة تعلّم. خلالها نجهّز كل شيء ونربط القياس ونبدأ العمل، وفي نهايتها تكون هناك نقطة بداية مقاسة لديكم. "
        "عليها فقط نحدّد معًا هدفًا رقميًا. قبل ذلك لن نعطيكم رقمًا للنتائج: ليست لدينا بعد بيانات خاصة بكم نبني عليها، ولا نستخدم "
        "متوسطات عامة. وإن لم تكفِ البيانات بعد 30 يومًا فسنقول ذلك ولن نختلق رقمًا.",
        "Первые 30 дней — период обучения. За это время мы всё настраиваем, подключаем измерение и начинаем работать, и в конце "
        "появляется точка отсчёта, измеренная у вас. Только на ней мы вместе ставим числовую цель. До этого мы не назовём вам "
        "цифру результата: у нас ещё нет ваших данных, на которые её можно опереть, и общими средними мы не пользуемся. Если через "
        "30 дней данных всё ещё мало, мы так и скажем и не станем выдумывать цифру."),
    "measured_title": _five("מה אנחנו מודדים", "What we measure", "Ce que nous mesurons", "ما الذي نقيسه", "Что мы измеряем"),
    "m_work": _five("מה פורסם ומה בוצע בכל שבוע, בדוח שבועי אצלכם בדשבורד", "what was published and done each week, in a weekly report in your dashboard",
                    "ce qui a été publié et fait chaque semaine, dans un rapport hebdomadaire sur votre tableau de bord",
                    "ما نُشر وما أُنجز كل أسبوع، في تقرير أسبوعي في لوحتكم", "что опубликовано и сделано за неделю — в еженедельном отчёте в вашем кабинете"),
    "m_enquiries": _five("כמה אנשים פנו אליכם דרך האתר ודף הנחיתה", "how many people contacted you through the site and the landing page",
                         "combien de personnes vous ont contacté via le site et la page d'atterrissage",
                         "كم شخصًا تواصل معكم عبر الموقع وصفحة الهبوط", "сколько людей обратились к вам через сайт и посадочную страницу"),
    "m_ads": _five("כמה כסף יצא על פרסום, ומה נמדד שהגיע ממנו", "how much was spent on advertising, and what was measured as coming from it",
                   "combien a été dépensé en publicité, et ce qui a été mesuré comme en provenant",
                   "كم أُنفق على الإعلان، وما الذي قيس أنه جاء منه", "сколько потрачено на рекламу и что измерено как пришедшее с неё"),
    "m_organic": _five("המיקום שלכם בגוגל בביטויים שתאשרו", "your position on Google for the phrases you approve",
                       "votre position sur Google pour les expressions que vous approuverez",
                       "موقعكم في Google للعبارات التي توافقون عليها", "ваша позиция в Google по фразам, которые вы утвердите"),
    "own_title": _five("מה שמסרתם לנו (הנתונים שלכם, לא הערכה שלנו)", "What you told us (your own figures, not our estimate)",
                       "Ce que vous nous avez indiqué (vos chiffres, pas notre estimation)",
                       "ما ذكرتموه لنا (أرقامكم أنتم، لا تقديرنا)", "Что вы нам сообщили (ваши данные, а не наша оценка)"),
    "own_budget": _five("תקציב חודשי לשיווק", "Monthly marketing budget", "Budget marketing mensuel", "ميزانية التسويق الشهرية",
                        "Месячный бюджет на маркетинг"),
    "own_goal": _five("המטרה החשובה לכם ב-3 החודשים הקרובים", "Your most important goal for the next 3 months",
                      "Votre objectif principal pour les 3 prochains mois", "أهم هدف لكم في الأشهر الثلاثة القادمة",
                      "Ваша главная цель на ближайшие 3 месяца"),
    "no_figure": _five(
        "מספר של תוצאות לא אתן לכם עכשיו: אין לנו נתון ענפי שאפשר לעמוד מאחוריו, ו-30 הימים הראשונים הם שקובעים את נקודת ההתחלה שלכם.",
        "I will not give you a results number now: we hold no industry figure we can stand behind, and the first 30 days are what establish your starting point.",
        "Je ne vous donnerai pas de chiffre de résultat maintenant : nous n'avons aucun chiffre sectoriel que nous puissions garantir, et ce sont les 30 premiers jours qui établissent votre point de départ.",
        "لن أعطيكم رقمًا للنتائج الآن: لا نملك رقمًا قطاعيًا نستطيع الوقوف خلفه، وأول 30 يومًا هي التي تحدّد نقطة بدايتكم.",
        "Цифру результата я сейчас не назову: у нас нет отраслевого показателя, за который мы можем ручаться, а точку отсчёта задают первые 30 дней."),
    "a_report": _five("דוח שבועי בדשבורד על מה שבוצע", "A weekly report in your dashboard on what was done",
                      "Un rapport hebdomadaire sur votre tableau de bord", "تقرير أسبوعي في لوحتكم عمّا أُنجز",
                      "Еженедельный отчёт в кабинете о сделанном"),
    "a_setup": _five("דף נחיתה, חיבור החשבונות וחיבור המדידה", "A landing page, connecting your accounts and connecting the measurement",
                     "Une page d'atterrissage, la connexion de vos comptes et de la mesure", "صفحة هبوط وربط الحسابات وربط القياس",
                     "Посадочная страница, подключение аккаунтов и измерения"),
    "a_research": _five("מחקר שוק ומתחרים ובדיקת הנוכחות הקיימת שלכם, כדוח כתוב", "Market and competitor research and an audit of your existing presence, as a written report",
                        "Une étude de marché et des concurrents et un audit de votre présence actuelle, sous forme de rapport écrit",
                        "بحث السوق والمنافسين ومراجعة حضوركم الحالي، كتقرير مكتوب", "Исследование рынка и конкурентов и аудит вашего присутствия — письменным отчётом"),
    "a_content": _five("3 פוסטים בשבוע ו-2 מאמרים בחודש, מפורסמים לפי לוח קבוע", "3 posts a week and 2 articles a month, published on a fixed schedule",
                       "3 publications par semaine et 2 articles par mois, publiés selon un calendrier fixe",
                       "3 منشورات أسبوعيًا ومقالان شهريًا، تُنشر وفق جدول ثابت", "3 поста в неделю и 2 статьи в месяц по фиксированному графику"),
    "a_ads": _five("העלאת הקמפיינים וטיפול שוטף בהם", "Launching the campaigns and looking after them continuously",
                   "Le lancement des campagnes et leur suivi continu", "إطلاق الحملات ومتابعتها باستمرار", "Запуск кампаний и постоянная работа с ними"),
    "a_organic": _five("עבודה חודשית על ביטויי החיפוש והמאמרים; תוצאות אורגניות נבנות לאורך חודשים", "Monthly work on the search phrases and the articles; organic results build over months",
                       "Un travail mensuel sur les expressions de recherche et les articles ; les résultats organiques se construisent sur des mois",
                       "عمل شهري على عبارات البحث والمقالات؛ النتائج المجانية تُبنى على مدى أشهر", "Ежемесячная работа над поисковыми фразами и статьями; органический результат строится месяцами"),
}
AD_SERVICES = ("google", "meta", "tiktok", "youtube")
OWN_FIELDS = (("marketing_budget", "own_budget"), ("main_goal", "own_goal"))


def pick(key: str, lang: str) -> str:
    return T[key].get(lang if lang in LANGS else "he")


def numbers(text) -> list:
    """Every number in the text, normalised ("1,000" and "1 000" are 1000)."""
    return [re.sub(r"[,   ]", "", m) for m in NUMBER.findall(str(text or ""))]


def _walk_numbers(node, out: set):
    if isinstance(node, dict):
        for value in node.values():
            _walk_numbers(value, out)
    elif isinstance(node, (list, tuple)):
        for value in node:
            _walk_numbers(value, out)
    elif isinstance(node, bool) or node is None:
        return
    elif isinstance(node, (int, float)):
        out.add(str(int(node)) if float(node) == int(node) else str(node))
    else:
        out.update(numbers(node))


def package_numbers(packages, price_list=None) -> set:
    """What a number may rest on as `package`: our price list, the offered
    packages' own amounts, and the plain counts of the service."""
    out = set(PLAIN)
    _walk_numbers(price_list, out)
    for pkg in packages or []:
        if isinstance(pkg, dict):                      # amounts only: a number inside a package's free text proves nothing
            _walk_numbers([v for v in pkg.values() if not isinstance(v, str)], out)
            _walk_numbers([v for v in (pkg.get("setup_fee_breakdown") or {}).values()], out)
            _walk_numbers([v for v in (pkg.get("monthly_breakdown") or {}).values()], out)
    return out


def prospect_numbers(answers) -> set:
    out = set()
    _walk_numbers(answers, out)
    return out


def basis_of(sentence: str, package: set, own: set, actions_only: bool = False):
    """[{"value", "basis": {"kind"}}] for a sentence that may be said, or None
    when it may not. THE rule of this module."""
    text = str(sentence or "")
    if BENCHMARK.search(text):
        return None
    found, result = numbers(text), bool(OUTCOME.search(text))
    out = []
    for value in found:
        if result:
            # a number next to a result is the prospect's own figure, said as theirs — or it is not said
            if actions_only or value not in own or not THEIRS.search(text):
                return None
            out.append({"value": value, "basis": {"kind": "prospect"}})
        elif value in own:
            out.append({"value": value, "basis": {"kind": "prospect"}})
        elif value in package:
            out.append({"value": value, "basis": {"kind": "package"}})
        else:
            return None
    return out


def clean_text(text, package: set, own: set, log: dict, where: str, actions_only: bool = False) -> str:
    kept = []
    for sentence in [s for s in SENTENCE.split(str(text or "")) if s.strip()]:
        bases = basis_of(sentence, package, own, actions_only)
        if bases is None:
            log["removed"].append({"where": where, "text": sentence.strip()[:200]})
            continue
        log["figures"].extend({"where": where, **b} for b in bases)
        kept.append(sentence.strip())
    return " ".join(kept)


def _services(packages) -> set:
    return {str(s).lower() for pkg in packages or [] if isinstance(pkg, dict) for s in pkg.get("recommended_services") or []}


def measured(packages, lang: str) -> dict:
    services = _services(packages)
    items = [pick("m_work", lang), pick("m_enquiries", lang)]
    if services & set(AD_SERVICES):
        items.append(pick("m_ads", lang))
    if any("organic" in s or "seo" in s for s in services):
        items.append(pick("m_organic", lang))
    return {"title": pick("measured_title", lang), "items": items}


def first_period(lang: str) -> dict:
    return {"title": pick("first_title", lang), "text": pick("first_text", lang), "days": LEARNING_DAYS,
            "basis": {"kind": "package"}}


def own_figures(answers, lang: str) -> dict:
    """The prospect's own figures, shown as theirs. {} when they gave none."""
    answers = answers if isinstance(answers, dict) else {}
    items = [{"label": pick(label, lang), "value": str(answers[key]).strip()[:120], "basis": {"kind": "prospect"}}
             for key, label in OWN_FIELDS if str(answers.get(key) or "").strip()]
    return {"title": pick("own_title", lang), "items": items} if items else {}


def fixed_actions(packages, lang: str, min_setup_fee=None) -> list:
    """What the packages contain, in fixed words — used when too little of the
    model's own list survived."""
    services = _services(packages)
    standard = bool(packages) and min_setup_fee is not None and all(
        float((p or {}).get("setup_fee_total") or 0) >= float(min_setup_fee) for p in packages)
    keys = (["a_setup", "a_research", "a_content"] if standard else [])
    if services & set(AD_SERVICES):
        keys.append("a_ads")
    if any("organic" in s or "seo" in s for s in services):
        keys.append("a_organic")
    return [pick(k, lang) for k in keys + ["a_report"]]


def _price_list():
    from agents.onboarding_agent import PRICING
    return PRICING


def enforce(proposal: dict, answers=None, lang: str = None, prospect: bool = True) -> dict:
    """The proposal as a prospect may read it. Runs on every proposal that
    ships (agents/qa_agent_content._enforce_invariants) and is idempotent.
    `prospect=False` is an existing client's upgrade proposal: the same number
    rule, without the new-client first-period block."""
    if not isinstance(proposal, dict):
        return proposal
    for field in REMOVED_FIELDS:
        proposal.pop(field, None)
    if lang not in LANGS:
        from core import vendor_names
        lang = vendor_names.guess_lang(proposal.get("business_summary") or proposal.get("honest_note") or "")
        lang = lang if lang in LANGS else "he"
    packages = [p for p in proposal.get("packages") or [] if isinstance(p, dict)]
    price_list = _price_list()
    package, own = package_numbers(packages, price_list), prospect_numbers(answers)
    log = {"removed": [], "figures": []}
    for field in ("business_summary", "honest_note", "scarcity_note", "rejection_reason"):
        if isinstance(proposal.get(field), str):
            proposal[field] = clean_text(proposal[field], package, own, log, field)
    for field, actions in (("goals_90_days", True), ("cost_disclaimers", False)):
        items = proposal.get(field) if isinstance(proposal.get(field), list) else []
        proposal[field] = [t for t in (clean_text(item, package, own, log, field, actions_only=actions) for item in items) if t]
    for pkg in packages:
        for field in ("description", "manual_followup_note"):
            if isinstance(pkg.get(field), str):
                pkg[field] = clean_text(pkg[field], package, own, log, f"package.{field}")
    if proposal.get("approved") and len(proposal["goals_90_days"]) < 3:
        fixed = fixed_actions(packages, lang, (price_list or {}).get("min_setup_fee"))
        proposal["goals_90_days"] += [line for line in fixed if line not in proposal["goals_90_days"]]
    if proposal.get("approved") and prospect:
        proposal["first_period"] = first_period(lang)
        proposal["measured"] = measured(packages, lang)
        proposal["your_figures"] = own_figures(answers, lang)
    proposal["figures"] = log["figures"]
    if log["removed"]:
        print(f"[sales_truth] removed {len(log['removed'])} sentence(s) with no basis: "
              f"{[r['where'] for r in log['removed']]}")
    return proposal


def problems(proposal: dict, answers=None) -> list:
    """What is still wrong with a proposal ([] = a prospect may read it)."""
    out = [f"{field}_present" for field in REMOVED_FIELDS if proposal.get(field)]
    packages = [p for p in proposal.get("packages") or [] if isinstance(p, dict)]
    package, own = package_numbers(packages, _price_list()), prospect_numbers(answers)
    texts = [(f, proposal.get(f)) for f in ("business_summary", "honest_note", "scarcity_note", "rejection_reason")]
    texts += [("goals_90_days", t) for t in proposal.get("goals_90_days") or []]
    texts += [("cost_disclaimers", t) for t in proposal.get("cost_disclaimers") or []]
    texts += [(f"package.{f}", p.get(f)) for p in packages for f in ("description", "manual_followup_note")]
    for where, text in texts:
        for sentence in [s for s in SENTENCE.split(str(text or "")) if s.strip()]:
            if basis_of(sentence, package, own, actions_only=where == "goals_90_days") is None:
                out.append(f"no_basis:{where}")
    if proposal.get("approved") and "first_period" in proposal and not (proposal["first_period"] or {}).get("text"):
        out.append("first_period_missing")
    return out


def guard_reply(reply: str, packages, answers, lang: str = None) -> str:
    """A closer's free-text reply: a sentence with a number or a market norm
    that has no basis is replaced by the one true sentence about it."""
    if lang not in LANGS:
        from core import vendor_names
        lang = vendor_names.guess_lang(reply or "")
        lang = lang if lang in LANGS else "he"
    packages = [p for p in packages or [] if isinstance(p, dict)]
    log = {"removed": [], "figures": []}
    text = clean_text(reply, package_numbers(packages, _price_list()), prospect_numbers(answers), log, "reply")
    if log["removed"]:
        print(f"[sales_truth] reply: removed {len(log['removed'])} sentence(s) with no basis")
        text = (text + " " + pick("no_figure", lang)).strip()
    return text


def stored(proposal) -> dict:
    """A proposal already on file (written before this rule), as an AGENT may
    be given it: the invented market paragraph and result targets are not passed
    on, so nobody repeats them to a client who has already bought."""
    proposal = dict(proposal) if isinstance(proposal, dict) else {}
    for field in REMOVED_FIELDS:
        proposal.pop(field, None)
    goals = proposal.get("goals_90_days") if isinstance(proposal.get("goals_90_days"), list) else []
    proposal["goals_90_days"] = [g for g in goals if not (numbers(g) and OUTCOME.search(str(g))) and not BENCHMARK.search(str(g))]
    return proposal


def guard_reaction(text: str, answer_text: str) -> str:
    """The one-line reaction between two questions: shown whole, or not at all.
    The only numbers it may carry are the ones in the answer it reacts to."""
    log = {"removed": [], "figures": []}
    clean = clean_text(text, set(), prospect_numbers(answer_text), log, "reaction")
    return "" if log["removed"] else clean
