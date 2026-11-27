"""How agents speak to clients (2026-11-28, knowledge/client-governance.md §9).

A client never reads a platform's internal vocabulary. THE table of those
terms lives here, as data: what is forbidden (in Latin letters and in
transliteration), and the plain words that replace it in each of the five
languages. Adding a platform = adding rows to TERMS. Nothing about a term is
written in a prompt or in another module.

Enforced on the OUTPUT, never trusted to a prompt:

- `find(text, lang)`      the forbidden terms in a text, by name.
- `expectation_problems`  an expected result stated with no basis (§9 point 4).
- `enforce(text, lang)`   what every client-facing message and card passes:
      a problem is logged BY NAME, the text is rewritten (the caller's
      rewriter first, then the table's own plain words), re-checked, and
      `Blocked` when it still fails — it does not reach the client.
- `plain(text, lang)`     the deterministic rewrite: a term with plain words
      becomes them — SHORT (a noun phrase) inside a sentence, LONG (the
      explanation) standing alone; a term that is never mentioned takes its
      sentence with it. It does not fix agreement around the phrase: the
      model rewrite, tried first, is what makes it read naturally.
- `where(campaign_type)`  where a campaign appears, in places a client knows.

Two limits (§9): CLIENT-FACING output only — admin views and agent-to-agent
text are never passed through here. And it is not concealment: a term the
client used themselves is theirs to hear back (`allow_from` their message),
so "is this Performance Max?" gets a plain yes and the explanation.

What this is NOT: understanding. It matches words. A term nobody listed
passes, and the expectation check reads sentence shapes — it can miss a
promise phrased another way and it can flag a sentence that was fine.
"""
import contextvars
import re

SERVICE_NAME = "client_speech"
LANGS = ("he", "en", "fr", "ar", "ru")


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


# ─── THE table ───────────────────────────────────────────────────────────────
# key: (patterns, plain words | None, languages where the LATIN word is ordinary speech)
#   patterns   regex fragments; a space matches any run of spaces / hyphens / dots,
#              an apostrophe any geresh. Latin is case-insensitive and word-bounded;
#              Hebrew may carry its one-letter prefixes (ב/ל/ה/ו/ש/מ/כ).
#   plain      the words a client reads instead. None = never mentioned in any form.
#   ordinary   e.g. "conversion" is an English and a French word: there only the
#              transliterations and product names are forbidden.
TERMS = {
    # ── Google: where a campaign runs ──
    "pmax": ((r"performance max", r"p max", r"pmax", r"פרפורמא?נס מקס", r"פי מקס", r"بي?رفورما?نس ماكس", r"перформанс макс"),
             _five("קמפיין שרץ בכל המקומות של גוגל — חיפוש, יוטיוב, ג'ימייל, מפות ואתרים — וגוגל מחליטה איפה להציג לפי מי שהכי סביר לקנות",
                   "a campaign that runs everywhere on Google — search, YouTube, Gmail, Maps and websites — where Google decides where to show it by who is most likely to buy",
                   "une campagne diffusée partout chez Google — recherche, YouTube, Gmail, Maps et sites web — où Google choisit où l'afficher selon qui a le plus de chances d'acheter",
                   "حملة تظهر في كل أماكن جوجل — البحث ويوتيوب وجيميل والخرائط والمواقع — وجوجل تقرر أين تعرضها بحسب من هو الأقرب للشراء",
                   "кампания, которая идёт везде в Google — в поиске, на YouTube, в Gmail, на Картах и на сайтах, — а Google решает, где показывать, по тому, кто вероятнее купит"), ()),
    "search": ((r"rsa", r"responsive search ads?", r"search ads? campaigns?", r"אר אס איי", r"רספונסיב סרץ'?"),
               _five("מודעות בתוצאות החיפוש של גוגל, כשמישהו מחפש בדיוק את מה שאתם מוכרים",
                     "ads in Google's search results, when someone searches for exactly what you sell",
                     "des annonces dans les résultats de recherche Google, quand quelqu'un cherche exactement ce que vous vendez",
                     "إعلانات في نتائج بحث جوجل، عندما يبحث أحدهم عمّا تبيعونه بالضبط",
                     "объявления в результатах поиска Google, когда человек ищет именно то, что вы продаёте"), ()),
    "demand_gen": ((r"demand gen(?:eration)?", r"דימא?נד ג'?ן", r"ديماند جي?ن", r"д[иеэ]манд дж[еэ]н"),
                   _five("מודעות עם תמונות ביוטיוב, בג'ימייל ובפיד של גוגל, לאנשים שעוד לא חיפשו אתכם",
                         "ads with images on YouTube, in Gmail and in Google's feed, for people who have not searched for you yet",
                         "des annonces avec images sur YouTube, dans Gmail et dans le fil Google, pour des personnes qui ne vous ont pas encore cherché",
                         "إعلانات بالصور على يوتيوب وفي جيميل وفي خلاصة جوجل، لأشخاص لم يبحثوا عنكم بعد",
                         "объявления с картинками на YouTube, в Gmail и в ленте Google для людей, которые вас ещё не искали"), ()),
    "display": ((r"display (?:campaigns?|ads?|network)", r"(?:campagnes?|réseau|annonces?) display", r"gdn", r"דיספליי"),
                _five("באנרים באתרים ובאפליקציות", "banners on websites and in apps", "des bannières sur des sites et dans des applications",
                      "لافتات إعلانية في المواقع والتطبيقات", "баннеры на сайтах и в приложениях"), ()),
    "youtube": ((r"in stream", r"אין סטרים", r"إن ستريم", r"ин стрим"),
                _five("סרטון שרץ לפני או בתוך סרטונים ביוטיוב", "a video that plays before or during videos on YouTube",
                      "une vidéo diffusée avant ou pendant les vidéos sur YouTube", "فيديو يُعرض قبل الفيديوهات على يوتيوب أو خلالها",
                      "ролик, который идёт перед видео на YouTube или внутри них"), ()),
    "bumper": ((r"bumper(?: ads?)?", r"באמפר", r"بامبر", r"бампер"),
               _five("סרטון של שש שניות ביוטיוב שאי אפשר לדלג עליו", "a six-second video on YouTube that cannot be skipped",
                     "une vidéo de six secondes sur YouTube qu'on ne peut pas passer", "فيديو من ست ثوانٍ على يوتيوب لا يمكن تخطيه",
                     "шестисекундный ролик на YouTube, который нельзя пропустить"), ()),
    "shorts_ads": ((r"shorts ads?", r"שורטס אדס"),
                   _five("סרטון אנכי בין הסרטונים הקצרים של יוטיוב", "a vertical video between YouTube's short videos",
                         "une vidéo verticale entre les vidéos courtes de YouTube", "فيديو عمودي بين الفيديوهات القصيرة على يوتيوب",
                         "вертикальный ролик между короткими видео YouTube"), ()),
    "remarketing": ((r"re ?marketing", r"re ?targeting", r"רי ?מרקטינג", r"רי ?טרגטינג", r"ريماركتي?نج", r"ремаркетинг\w*", r"ретаргетинг\w*"),
                    _five("מודעות שחוזרות לאנשים שכבר ביקרו באתר שלכם", "ads that return to people who already visited your site",
                          "des annonces qui reviennent vers les personnes ayant déjà visité votre site",
                          "إعلانات تعود إلى أشخاص زاروا موقعكم من قبل", "объявления, которые возвращаются к людям, уже побывавшим на вашем сайте"), ()),
    # ── Google: the numbers ──
    "cpc": ((r"cpc", r"סי פי סי"),
            _five("כמה עולה כל קליק על המודעה", "what each click on the ad costs", "ce que coûte chaque clic sur l'annonce", "كم تكلّف كل نقرة على الإعلان", "сколько стоит каждый клик по объявлению"), ()),
    "cpm": ((r"cpm", r"סי פי אם"),
            _five("כמה עולה להגיע לאלף אנשים", "what it costs to reach a thousand people", "ce que coûte le fait de toucher mille personnes",
                  "كم يكلّف الوصول إلى ألف شخص", "сколько стоит показ тысяче человек"), ()),
    "ctr": ((r"ctr", r"סי טי אר"),
            _five("כמה אחוז מהאנשים שראו את המודעה לחצו עליה", "what share of the people who saw the ad clicked it",
                  "quelle part des personnes ayant vu l'annonce ont cliqué", "نسبة من نقروا على الإعلان ممن شاهدوه",
                  "какая доля увидевших объявление нажала на него"), ()),
    "roas": ((r"roas", r"רו ?אס"),
             _five("כמה שקלים חוזרים על כל שקל שהושקע", "how much comes back for every shekel invested",
                   "combien revient pour chaque shekel investi", "كم يعود مقابل كل شيكل مستثمَر", "сколько возвращается на каждый вложенный шекель"), ()),
    "conversion": ((r"conversions?", r"קונבר(?:ז'?|ש)(?:ן|נים)"),
                   _five("הפעולה שרצינו שתקרה: קנייה, פנייה או הזמנה", "the action we wanted to happen: a purchase, an enquiry or a booking",
                         "l'action que nous voulions obtenir : un achat, une demande ou une réservation",
                         "الفعل الذي أردنا حدوثه: شراء أو استفسار أو حجز", "действие, которого мы добивались: покупка, заявка или заказ"),
                   ("en", "fr")),
    "impressions": ((r"impressions?", r"אימפרש(?:ן|נס|נים)"),
                    _five("כמה פעמים המודעה הוצגה", "how many times the ad was shown", "combien de fois l'annonce a été affichée",
                          "كم مرة عُرض الإعلان", "сколько раз объявление было показано"), ("en", "fr")),
    # ── never mentioned, in any form ──
    "asset_group": ((r"asset groups?", r"אסט גרופ", r"קבוצ(?:ת|ות) (?:ה)?נכסים", r"групп\w+ объектов"), None, ()),
    "ad_strength": ((r"ad strength", r"אד סטרנג'?ת'?", r"חוזק (?:ה)?מודע(?:ה|ות)", r"עוצמת (?:ה)?מודע(?:ה|ות)"), None, ()),
    "bid_strategy": ((r"bid(?:ding)? strateg(?:y|ies)", r"smart bidding", r"ביד(?:ינג)? סטרטג'?י", r"אסטרטגיי?ת (?:ה)?בידינג",
                      r"אסטרטגיי?ת (?:ה)?הצעות (?:ה)?מחיר", r"стратеги\w+ назначения ставок"), None, ()),
    "match_type": ((r"(?:keyword )?match types?", r"(?:broad|phrase|exact) match", r"מאץ'? טייפ", r"סוגי? (?:ה)?התאמה",
                    r"התאמה (?:רחבה|מדויקת)", r"התאמת ביטוי", r"тип\w* соответствия"), None, ()),
    "manifest": ((r"manifests?", r"מניפסט"), None, ()),
    "render": ((r"render(?:s|ed|ing)?", r"רינדור(?:ים)?", r"רנדר"), None, ()),
    "agent_module": ((r"[a-z]+(?:_[a-z]+)*_agent",), None, ()),
}

# The SECOND column of the table (the owner, 2026-11-29). Every term that has
# plain words has them in two forms:
#   long   (TERMS[key][1], above) — the full explanation: a first explanation, or
#          the term standing alone ("what is X", a line that is only the term).
#   short  (here) — a NOUN PHRASE that fits inside a sentence, where the term stood.
# An explanation cannot stand where a noun belongs: "ה-CTR עלה ל-3%" becomes
# "אחוז ההקלקות עלה ל-3%", never the long form. A row without both forms fails at import.
SHORT = {
    "pmax": _five("קמפיין בכל המקומות של גוגל", "a campaign across all of Google", "une campagne sur l'ensemble de Google",
                  "حملة في كل أماكن جوجل", "кампания по всему Google"),
    "search": _five("מודעות בחיפוש של גוגל", "ads in Google search", "des annonces dans la recherche Google",
                    "إعلانات في بحث جوجل", "объявления в поиске Google"),
    "demand_gen": _five("מודעות תמונה ביוטיוב ובג'ימייל", "image ads on YouTube and Gmail",
                        "des annonces illustrées sur YouTube et Gmail", "إعلانات مصوّرة على يوتيوب وجيميل",
                        "объявления с картинками на YouTube и в Gmail"),
    "display": _five("באנרים באתרים", "banners on websites", "des bannières sur des sites", "لافتات إعلانية في المواقع",
                     "баннеры на сайтах"),
    "youtube": _five("סרטון פרסומת ביוטיוב", "a video ad on YouTube", "une publicité vidéo sur YouTube",
                     "إعلان فيديو على يوتيوب", "видеореклама на YouTube"),
    "bumper": _five("סרטון פרסומת קצר ביוטיוב", "a short video ad on YouTube", "une courte publicité vidéo sur YouTube",
                    "إعلان فيديو قصير على يوتيوب", "короткий рекламный ролик на YouTube"),
    "shorts_ads": _five("סרטון פרסומת אנכי ביוטיוב", "a vertical video ad on YouTube",
                        "une publicité vidéo verticale sur YouTube", "إعلان فيديو عمودي على يوتيوب",
                        "вертикальный рекламный ролик на YouTube"),
    "remarketing": _five("מודעות למי שכבר ביקר באתר", "ads for people who already visited the site",
                         "des annonces pour vos anciens visiteurs", "إعلانات لمن زار الموقع من قبل",
                         "объявления для прежних посетителей сайта"),
    "cpc": _five("עלות לקליק", "cost per click", "coût par clic", "تكلفة النقرة", "цена клика"),
    "cpm": _five("עלות לאלף חשיפות", "cost per thousand views", "coût pour mille affichages", "تكلفة الألف ظهور",
                 "цена за тысячу показов"),
    "ctr": _five("אחוז ההקלקות", "click rate", "taux de clics", "نسبة النقر", "доля кликов"),
    "roas": _five("החזר על ההוצאה", "return on ad spend", "retour sur les dépenses publicitaires",
                  "العائد على الإنفاق الإعلاني", "окупаемость рекламы"),
    "conversion": _five("פעולות של לקוחות", "customer actions", "actions de clients", "إجراءات العملاء", "целевые действия"),
    "impressions": _five("חשיפות", "times shown", "affichages", "مرات الظهور", "показы"),
}
FORMS = ("short", "long")
if set(SHORT) != {key for key, row in TERMS.items() if row[1] is not None} or not all(
        (SHORT[key].get(lang) or "").strip() and (TERMS[key][1].get(lang) or "").strip() for key in SHORT for lang in LANGS):
    raise RuntimeError("client_speech: every term with plain words needs a short AND a long form in all five languages")


def words(key: str, lang: str = "he", form: str = "short") -> str:
    """A term's plain words in one form ('' = no row, or a term that is never mentioned)."""
    row = TERMS.get(key)
    if not row or row[1] is None:
        return ""
    lang = lang if lang in LANGS else "he"
    return (SHORT[key] if form == "short" else row[1])[lang]

# ─── Matching ────────────────────────────────────────────────────────────────
_HE_PREFIX = r"(?:[ושה]?[בלמכ]?ה?)"
_SEP = r"[\s\-־._]*"
_GERESH = r"['׳’ʼ]"
_URL = re.compile(r"(https?://\S+|www\.\S+)")
_HE_HYPHEN_PREFIX = re.compile(r"(?<![֐-׿])([ושה]?[בלמכ]?ה?)[-־]$")      # the "ה-" of "ה-CTR"


def _compile(fragment: str):
    body = fragment.replace("'", _GERESH).replace(" ", _SEP)
    if re.search(r"[֐-׿]", fragment):
        return re.compile(r"(?<![֐-׿])" + _HE_PREFIX + r"(?:" + body + r")(?![֐-׿])")
    if re.search(r"[؀-ۿ]", fragment):
        return re.compile(r"(?<![؀-ۿ])(?:ال)?(?:" + body + r")(?![؀-ۿ])")
    if re.search(r"[Ѐ-ӿ]", fragment):
        return re.compile(r"(?<![Ѐ-ӿ])(?:" + body + r")(?![Ѐ-ӿ])", re.IGNORECASE)
    return re.compile(r"(?<![A-Za-z0-9_])(?:" + body + r")(?![A-Za-z0-9_])", re.IGNORECASE)


_LATIN_FIRST = re.compile(r"^[a-z(\[]", re.IGNORECASE)
_COMPILED = {key: [(_compile(f), bool(_LATIN_FIRST.match(f))) for f in row[0]] for key, row in TERMS.items()}
NEVER = tuple(key for key, row in TERMS.items() if row[1] is None)

PROMPT_RULE = ("SPEAKING TO THE CLIENT (governance §9): never use a platform's internal vocabulary in anything the client "
               "reads — no product names, no acronyms, no metric jargon, no names of our own internals. Say where the ad "
               "appears and what the number means in plain words. If the client asks about a term directly, answer plainly "
               "and explain it. An expected result is never a bare number: say what it rests on (their own past "
               "performance, a named industry benchmark, or an estimate given as a RANGE with its assumptions). "
               "The system checks the text you write and rewrites it when this fails.")

_allowed = contextvars.ContextVar("uallak_client_speech_allowed", default=())


def guess_lang(text: str) -> str:
    from core import vendor_names
    return vendor_names.guess_lang(text)


def _spans(text: str, lang: str, allowed) -> list:
    """[(start, end, key)] of every forbidden term outside links, longest first at a position."""
    text = str(text or "")
    allowed = set(allowed or ()) | set(_allowed.get() or ())
    hits, offset = [], 0
    for i, piece in enumerate(_URL.split(text)):
        if not i % 2:
            for key, patterns in _COMPILED.items():
                if key in allowed:
                    continue
                for pattern, latin in patterns:
                    if latin and lang in TERMS[key][2]:
                        continue
                    hits.extend((offset + m.start(), offset + m.end(), key) for m in pattern.finditer(piece))
        offset += len(piece)
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    out, end = [], -1
    for h in hits:
        if h[0] >= end:
            out.append(h)
            end = h[1]
    return out


def find(text: str, lang: str = None, allowed=()) -> list:
    """[{"key", "term"}] — each forbidden term as it is written in the text."""
    text = str(text or "")
    lang = lang if lang in LANGS else guess_lang(text)
    return [{"key": key, "term": text[a:b].strip()} for a, b, key in _spans(text, lang, allowed)]


def keys_in(text: str) -> tuple:
    """The term keys a text uses, in any language — what a client said themselves."""
    return tuple(sorted({key for _a, _b, key in _spans(str(text or ""), "", ())}))


def allow_from(client_message: str):
    """For the rest of this turn, the terms the CLIENT used are not forbidden:
    a direct question gets a plain answer (§9, 'never what we admit')."""
    _allowed.set(keys_in(client_message))


def where(campaign_type: str, lang: str = "he") -> str:
    """Where a campaign appears, in places the client recognises ('' = no row).
    Standing alone on its line, so the LONG form."""
    return words(campaign_type, lang, "long")


# ─── An expected result needs its basis (§9 point 4) ─────────────────────────
_SENTENCE = re.compile(r"[^.!?\n؟]+[.!?؟]*\n?|\n")
_RESULT = (r"(?:פניות|לידים|מכירות|הזמנות|קליקים|לקוחות|רכישות|צפיות|מבקרים|leads|enquiries|inquiries|sales|orders|bookings|clicks|"
           r"customers|purchases|views|visitors|prospects|ventes|commandes|réservations|clics|clients|demandes|"
           r"عملاء|مبيعات|طلبات|حجوزات|نقرات|استفسارات|заявок|заявки|продаж|заказов|кликов|клиентов|покупок)")
_NUMBER_RESULT = re.compile(r"\d[\d,.]*\s*(?:(?:עד|to|à|إلى|до|-|–|—)\s*\d[\d,.]*\s*)?(?:\S+\s+){0,2}?" + _RESULT, re.IGNORECASE)
_PROMISE = re.compile(
    r"נביא|תקבלו|תגיעו|נגיע ל|צפוי|מכוונים ל|נשיג|יביא|יניב|מצפים ל|אמור(?:ים)? להביא|"
    r"\bwe(?:'ll| will| expect| aim| are aiming)\b|\byou(?:'ll| will)\b|\bexpect(?:ed)?\b|\baiming\b|\bshould (?:bring|generate|get)\b|"
    r"\bnous (?:visons|prévoyons|obtiendrons|attendons)\b|\bvous (?:obtiendrez|recevrez)\b|\bprévu\w*\b|"
    r"سنحقق|ستحصلون|نتوقع|المتوقع|نستهدف|"
    r"получите|принес[её]м|ожидаем|ожидается|рассчитываем|нацелены", re.IGNORECASE)
_BASIS = re.compile(
    r"על בסיס|בהתבסס על|על סמך|לפי (?:ה)?(?:ביצועים|נתונים|תוצאות)|הערכה|מעריכים|ממוצע (?:ב|ה)?ענף|ממוצע ענפי|בהנחה ש|"
    r"\bbased on\b|\baccording to your\b|\bestimate[ds]?\b|\bbenchmark\b|\bindustry average\b|\bassuming\b|\bfrom your (?:own )?(?:past|last)\b|"
    r"\bsur la base\b|\bd'après vos\b|\bestimation\b|\bmoyenne du secteur\b|\ben supposant\b|"
    r"بناءً على|استنادًا إلى|تقدير|متوسط القطاع|بافتراض|"
    r"на основе|по вашим|оценк\w+|средн\w+ по отрасли|при условии|исходя из", re.IGNORECASE)
_ESTIMATE = re.compile(r"הערכה|מעריכים|בהנחה ש|\bestimate[ds]?\b|\bassuming\b|\bestimation\b|\ben supposant\b|تقدير|بافتراض|"
                       r"оценк\w+|при условии", re.IGNORECASE)
_RANGE = re.compile(r"\d[\d,.]*\s*(?:עד|to|à|إلى|до|-|–|—)\s*\d|(?:בין|between|entre|بين|от)\s+\d[\d,.]*\s*\S{0,3}\s*(?:ל-?|ל־|and|et|و|до)\s*\d",
                    re.IGNORECASE)


def sentences(text: str) -> list:
    return [s for s in _SENTENCE.findall(str(text or "")) if s]


def expectation_problems(text: str) -> list:
    """[{"code", "sentence"}] for each sentence that promises a number of
    results with nothing said about what it rests on (the sentence itself or
    the one before it), and for an estimate given as a single number."""
    out, before = [], ""
    for sentence in sentences(text):
        if _PROMISE.search(sentence) and _NUMBER_RESULT.search(sentence):
            scope = before + " " + sentence
            if not _BASIS.search(scope):
                out.append({"code": "expected_without_basis", "sentence": sentence.strip()})
            elif _ESTIMATE.search(scope) and not _RANGE.search(sentence):
                out.append({"code": "estimate_not_a_range", "sentence": sentence.strip()})
        if sentence.strip():
            before = sentence
    return out


# ─── Rewrite and enforce ─────────────────────────────────────────────────────

class Blocked(Exception):
    """The text still fails after the rewrite: it does not reach the client. .problems names why."""

    def __init__(self, problems: list):
        self.problems = problems
        super().__init__("; ".join(problem_lines(problems)))


def problems(text: str, lang: str = None, allowed=()) -> list:
    """Every reason this text may not reach a client: forbidden terms by name, unbased expectations."""
    return ([{"code": "forbidden_term", **hit} for hit in find(text, lang, allowed)] + expectation_problems(text))


def problem_lines(found: list) -> list:
    return [f"forbidden term \"{p['term']}\" ({p['key']})" if p["code"] == "forbidden_term"
            else f"{p['code'].replace('_', ' ')}: \"{p['sentence'][:120]}\"" for p in found or []]


def _drop(text: str, bad) -> str:
    kept = [s for s in sentences(text) if not bad(s)]
    return re.sub(r"[ \t]{2,}", " ", "".join(kept)).strip()


def plain(text: str, lang: str = None, allowed=()) -> str:
    """The deterministic rewrite. A sentence naming a never-mentioned term, or
    promising a result with no basis, is removed whole; every other term
    becomes the table's plain words — the SHORT form (a noun phrase) inside a
    sentence, the LONG form (the explanation) when the term is all the
    sentence holds."""
    text = str(text or "")
    lang = lang if lang in LANGS else guess_lang(text)
    unbased = {p["sentence"] for p in expectation_problems(text)}
    text = _drop(text, lambda s: s.strip() in unbased
                 or any(key in NEVER for _a, _b, key in _spans(s, lang, allowed)))
    done = []
    for sentence in sentences(text):
        spans = _spans(sentence, lang, allowed)
        rest, cut = sentence, 0
        for start, end, _key in spans:
            rest = rest[:start - cut] + rest[end - cut:]
            cut += end - start
        alone = len(spans) == 1 and not re.search(r"\w", re.sub(r"[\u05D0-\u05EA]{1,3}[-\u05BE]", "", rest))
        out, last = [], 0
        for start, end, key in spans:
            lead = len(sentence[start:end]) - len(sentence[start:end].lstrip())
            before = sentence[last:start + lead]
            # "ה-CTR" / "ב-CTR": the hyphen and the article go with the term; a preposition stays ("באחוז ההקלקות")
            before = _HE_HYPHEN_PREFIX.sub(lambda m: m.group(1).replace("\u05D4", ""), before)
            out.append(before + words(key, lang, "long" if alone else "short"))
            last = end
        done.append("".join(out) + sentence[last:])
    return "".join(done)

def enforce(text: str, lang: str = None, allowed=(), rewrite=None, where: str = "", alert=None) -> str:
    """The text a client may read. Clean text comes back untouched. Otherwise
    the failure is reported BY NAME, `rewrite(text, problem_lines)` (a model,
    when the caller has one) gets one try, then the table's own words; a text
    that still fails raises Blocked."""
    text = str(text or "")
    if not text.strip():
        return text
    lang = lang if lang in LANGS else guess_lang(text)
    found = problems(text, lang, allowed)
    if not found:
        return text
    lines = problem_lines(found)
    print(f"[{SERVICE_NAME}] {where or 'client text'} failed: {'; '.join(lines)[:500]}")
    if alert:
        try:
            alert(f"{where or 'client text'} failed the client-language check and was rewritten: {'; '.join(lines)[:400]}")
        except Exception:
            pass
    if rewrite:
        try:
            candidate = str(rewrite(text, lines) or "")
            if candidate.strip() and not problems(candidate, lang, allowed):
                return candidate
        except Exception as e:
            print(f"[{SERVICE_NAME}] rewrite failed ({type(e).__name__}) — the table's words are used")
    fixed = plain(text, lang, allowed)
    left = problems(fixed, lang, allowed)
    if left or not fixed.strip():
        raise Blocked(left or found)
    return fixed


def enforce_tree(node, lang: str = "he", where: str = "", alert=None) -> int:
    """`enforce` on every string VALUE of a dict / list, in place (a card). A
    value that cannot be made clean becomes empty. Returns how many changed."""
    changed = 0
    items = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
    for key, value in list(items):
        if isinstance(value, str):
            try:
                clean = enforce(value, lang, where=f"{where} {key}".strip(), alert=alert)
            except Blocked:
                clean = ""
            if clean != value:
                node[key] = clean
                changed += 1
        elif isinstance(value, (dict, list)):
            changed += enforce_tree(value, lang, where, alert)
    return changed


REWRITE_SYSTEM = """You rewrite ONE message that an Israeli marketing agency is about to send to a client (a business
owner, not a marketer). It failed a check, for the reasons listed. Rewrite it in the SAME language, same tone, same
length or shorter, keeping every fact, number, link and question.

Rules:
- Replace each forbidden term with its plain words from `plain_words`, which gives two forms. Inside a sentence use
  `short` — a noun phrase that stands exactly where the term stood — and fix the grammar around it (gender, number,
  article) so it reads as if a person wrote it. Use `long` only when the term stands alone or the message is
  explaining what it means. Never put the long form where a noun belongs. Do not keep the term, its acronym or a
  transliteration of it, not even in brackets.
- A term with no plain words is never mentioned at all: say the point without it or drop that detail.
- An expected result may stay ONLY with what it rests on, and only if that basis is already in the message. An
  estimate must be a range with its assumptions. If the message gives no basis, remove the promise — do not invent
  a basis, a number or a range.
- Change nothing else.

Return JSON only: {"text": "..."}"""


def model_rewriter(client_id: int, lang: str, call=None):
    """A `rewrite` for enforce(): one model call through the one JSON gate."""
    def rewrite(text, lines):
        import json
        found = find(text, lang)
        payload = {"language": lang, "message": text, "failed_because": lines,
                   "plain_words": {hit["term"]: ({form: words(hit["key"], lang, form) for form in FORMS}
                                                 if words(hit["key"], lang) else None) for hit in found}}
        if call is not None:
            answer = call(REWRITE_SYSTEM, json.dumps(payload, ensure_ascii=False))
        else:
            from core.claude_json import safe_claude_json_call
            answer = safe_claude_json_call(REWRITE_SYSTEM, json.dumps(payload, ensure_ascii=False), max_tokens=1500,
                                           client_id=client_id, cost_category="claude_support")
        return str((answer or {}).get("text") or "")
    return rewrite


def outbound(text: str, where: str = "") -> str:
    """The backstop on a stored message to a client (client_agent.log_communication):
    no model, the table's own words. '' = blocked — the caller stores nothing."""
    def alert(line):
        from core.agent_base import agent_alert
        agent_alert(SERVICE_NAME, [line])
    try:
        return enforce(text, where=where or "outbound message", alert=alert)
    except Blocked as e:
        alert(f"{where or 'outbound message'} BLOCKED — it did not reach the client: {e}"[:600])
        return ""
