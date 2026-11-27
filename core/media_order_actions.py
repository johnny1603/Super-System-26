"""The paid-campaign chain as ONE card (2026-11-28, docs/PAID-MEDIA.md §3.5) —
registered into agent_actions.ACTIONS.

`prepare_campaign` is what the manager (or יואב / ליאור) proposes when the
client asks for a campaign ("תכינו קמפיין"). Nothing else is asked of anyone:

  prepare   יואב decides the campaign (type, objective, audience, landing
            page, offer) from what the client said and what we know about the
            business — the card shows HIS decision and what it will produce.
  click     the order is issued (core/media_order) → the copy step writes the
            text assets (core/ad_copy) → ליאור produces the files
            (core/paid_media_production) → the delivery is checked from the
            files → handed to יואב ONLY when complete → יואב verifies it
            against the platform minimum on his own.

The copy runs BEFORE the media: it costs the client no credits, and a
campaign whose copy cannot be written must not spend them.

Nothing is uploaded to the ad platform here. The result says so.
"""
import json
import re

from core import ad_copy, media_order, paid_media_production
from core import google_ads_formats as gaf

SERVICE_NAME = "media_order_actions"
ACTION = "prepare_campaign"
URL_IN_TEXT = re.compile(r"https://[^\s\"'<>)\]]+", re.IGNORECASE)

DECIDE_SYSTEM = """You are the Google Ads specialist of uallak, an Israeli marketing agency. The client asked for a
campaign. YOU decide the campaign — its type and objective — and what it advertises. You do NOT decide how the
creative looks (the media agent does) and you do not write ad copy (the copywriter does).

You are given: the client's request, anything already fixed (`given` — keep those values exactly), what we know
about the business, the brand's audience variants, and campaign_types with what each one needs.

Decide:
- campaign_type: one of campaign_types. Match the objective and what the business can supply. Do not choose
  "youtube" unless the request is explicitly about a video campaign.
- objective: sales | leads | website_traffic | awareness.
- audience_segment: who this campaign is for, in a few English words. When the brand has audience variants, the
  segment must clearly be ONE side: say "consumers" for a b2c variant or "businesses" for a b2b one, never both.
- landing_page_url: ONLY an https address that appears in the data you were given or in the request. Never
  compose or guess an address. None there = "".
- offer: what is advertised, one sentence in `language`, only from the request and the business facts. Never
  invent a price, a discount or a deadline. Nothing to go on = "".
- why: one sentence in `language` — why this campaign type, in plain words for the business owner.

Return JSON only: {"campaign_type": "", "objective": "", "audience_segment": "", "landing_page_url": "",
"offer": "", "why": ""}"""


def decide(client_id: int, given: dict, lang: str, call=None, knowledge=None, facts=None) -> dict:
    """Yoav's decision: `given` (what the chat already fixed) completed by ONE
    model call when something is missing. A landing page the model returns is
    kept only when that address is in what it was shown."""
    fields = ("campaign_type", "objective", "audience_segment", "landing_page_url", "offer")
    out = {k: given[k] for k in fields if given.get(k)}
    if all(out.get(k) for k in fields):
        return {**out, "why": ""}
    if knowledge is None:
        from core import client_knowledge
        knowledge = client_knowledge.profile(client_id, "google")
    if facts is None:
        from core import post_brief
        facts = post_brief.load_context(client_id).get("brand_facts") or {}
    payload = {"language": lang, "request": given.get("request", ""), "given": out, "business": knowledge,
               "audience_variants": [{"segment": v.get("segment", ""), "applies_to": v.get("applies_to", "")}
                                     for v in facts.get("palette_variants") or []],
               "campaign_types": {t: {"needs_images": bool(gaf.campaign(t)["images"]),
                                      "needs_video": bool(gaf.campaign(t)["video"])} for t in gaf.CAMPAIGN_TYPES}}
    shown = json.dumps(payload, ensure_ascii=False, default=str)
    if call is None:
        from core.claude_json import safe_claude_json_call

        def call(system, user):
            return safe_claude_json_call(system, user, max_tokens=500, client_id=client_id,
                                         cost_category="claude_ads")
    answer = call(DECIDE_SYSTEM, shown) or {}
    for k in fields:
        if not out.get(k) and answer.get(k):
            out[k] = str(answer[k]).strip()
    url = out.get("landing_page_url", "")
    if url and not given.get("landing_page_url") and url.rstrip("/") not in {u.rstrip("/.,") for u in
                                                                             URL_IN_TEXT.findall(shown)}:
        print(f"[{SERVICE_NAME}] client {client_id}: a landing page that was not in the data was dropped")
        out["landing_page_url"] = ""
    return {**out, "why": str(answer.get("why") or "").strip()[:300]}


def build(aa) -> dict:
    _txt, ActionFailed = aa._txt, aa.ActionFailed

    def _pick(texts, lang):
        return aa._pick(texts, lang)

    types = {
        "pmax": _txt("קמפיין רב-ערוצי (חיפוש, יוטיוב, רשת המדיה, Gmail ומפות יחד)",
                     "a cross-channel campaign (Search, YouTube, Display, Gmail and Maps together)",
                     "une campagne multicanale (Recherche, YouTube, Display, Gmail et Maps ensemble)",
                     "حملة متعددة القنوات (البحث ويوتيوب وشبكة العرض وGmail والخرائط معًا)",
                     "кампания сразу на всех каналах (Поиск, YouTube, медийная сеть, Gmail и Карты)"),
        "demand_gen": _txt("קמפיין חשיפה ויצירת ביקוש (יוטיוב, Discover ו-Gmail)",
                           "a demand-generation campaign (YouTube, Discover and Gmail)",
                           "une campagne de génération de demande (YouTube, Discover et Gmail)",
                           "حملة لتوليد الطلب (يوتيوب وDiscover وGmail)",
                           "кампания для формирования спроса (YouTube, Discover и Gmail)"),
        "display": _txt("קמפיין באנרים ברשת המדיה", "a display campaign", "une campagne Display",
                        "حملة على شبكة العرض", "кампания в медийной сети"),
        "youtube": _txt("קמפיין וידאו ביוטיוב", "a YouTube video campaign", "une campagne vidéo YouTube",
                        "حملة فيديو على يوتيوب", "видеокампания на YouTube"),
        "search": _txt("קמפיין חיפוש (מודעות טקסט)", "a search campaign (text ads)",
                       "une campagne sur le Réseau de Recherche (annonces textuelles)",
                       "حملة على شبكة البحث (إعلانات نصية)", "поисковая кампания (текстовые объявления)"),
    }
    objectives = {
        "sales": _txt("מכירות", "sales", "ventes", "مبيعات", "продажи"),
        "leads": _txt("פניות", "leads", "prospects", "عملاء محتملون", "заявки"),
        "website_traffic": _txt("תנועה לאתר", "website traffic", "trafic vers le site", "زيارات للموقع", "трафик на сайт"),
        "awareness": _txt("חשיפה", "awareness", "notoriété", "وعي بالعلامة", "узнаваемость"),
    }
    ask = {
        "landing_page_url": _txt(
            "כדי להכין את הקמפיין חסר לי דבר אחד: לאיזה עמוד המודעות מובילות? שלחו לי כאן את הכתובת המלאה (https://…).",
            "One thing is missing to prepare the campaign: which page do the ads lead to? Send me the full address here (https://…).",
            "Il me manque une chose pour préparer la campagne : vers quelle page mènent les annonces ? Envoyez-moi ici l'adresse complète (https://…).",
            "ينقصني شيء واحد لتجهيز الحملة: إلى أي صفحة تقود الإعلانات؟ أرسلوا لي العنوان الكامل هنا (https://…).",
            "Чтобы подготовить кампанию, не хватает одного: на какую страницу ведут объявления? Пришлите сюда полный адрес (https://…)."),
        "offer": _txt(
            "כדי להכין את הקמפיין חסר לי דבר אחד: מה מפרסמים בו? מוצר, שירות או הצעה — במשפט אחד.",
            "One thing is missing to prepare the campaign: what does it advertise? A product, a service or an offer — in one sentence.",
            "Il me manque une chose pour préparer la campagne : que met-elle en avant ? Un produit, un service ou une offre — en une phrase.",
            "ينقصني شيء واحد لتجهيز الحملة: ماذا تعلن؟ منتج أو خدمة أو عرض — في جملة واحدة.",
            "Чтобы подготовить кампанию, не хватает одного: что рекламируем? Товар, услугу или предложение — одной фразой."),
        "audience_unresolved": _txt(
            "למותג שלכם יש יותר מפלטת צבעים אחת, לפי קהל. לאיזה קהל הקמפיין הזה פונה?",
            "Your brand has more than one colour palette, by audience. Which audience is this campaign for?",
            "Votre marque a plus d'une palette de couleurs, selon le public. À quel public s'adresse cette campagne ?",
            "لعلامتكم أكثر من لوحة ألوان بحسب الجمهور. لأي جمهور هذه الحملة؟",
            "У вашего бренда несколько палитр — по аудиториям. Для какой аудитории эта кампания?"),
        "audience_segment": _txt(
            "למי הקמפיין הזה פונה? משפט אחד על הקהל מספיק.", "Who is this campaign for? One sentence about the audience is enough.",
            "À qui s'adresse cette campagne ? Une phrase sur le public suffit.", "لمن هذه الحملة؟ تكفي جملة واحدة عن الجمهور.",
            "Для кого эта кампания? Достаточно одной фразы об аудитории."),
    }
    refusals = {
        "not_in_package": _txt(
            "קמפיינים בגוגל הם חלק משירות שלא כלול בחבילה שלכם, אז לא הכנתי כלום. רוצים שאכין הצעה להוספה שלו?",
            "Google campaigns are part of a service your package doesn't include, so nothing was prepared. Shall I prepare an offer to add it?",
            "Les campagnes Google font partie d'un service absent de votre forfait : rien n'a été préparé. Je vous prépare une offre pour l'ajouter ?",
            "حملات جوجل جزء من خدمة غير مشمولة في باقتكم، لذلك لم أجهّز شيئًا. هل أجهّز عرضًا لإضافتها؟",
            "Кампании в Google входят в услугу, которой нет в вашем пакете, поэтому ничего не подготовлено. Подготовить предложение?"),
        "decision_failed": _txt(
            "לא הצלחתי לגבש את הקמפיין הפעם, והצוות קיבל התראה. שום דבר לא הופק ולא נוצל. כתבו לי \"נסה שוב\".",
            "I couldn't shape the campaign this time, and the team was alerted. Nothing was produced or spent. Write \"try again\".",
            "Je n'ai pas pu définir la campagne cette fois ; l'équipe est prévenue. Rien n'a été produit ni dépensé. Écrivez « réessaie ».",
            "لم أتمكن من صياغة الحملة هذه المرة وتم تنبيه الفريق. لم يُنتج شيء ولم يُصرف شيء. اكتبوا \"حاول مجددًا\".",
            "В этот раз кампанию определить не удалось, команда уведомлена. Ничего не создано и не потрачено. Напишите «ещё раз»."),
        "copy_failed": _txt(
            "הטקסטים של המודעות לא נכנסו למגבלות התווים גם אחרי תיקון, אז עצרתי לפני ההפקה. שום קרדיט לא נוצל, והצוות קיבל התראה.",
            "The ad texts did not fit the character limits even after a fix, so I stopped before production. No credit was used, and the team was alerted.",
            "Les textes des annonces ne tenaient pas dans les limites de caractères, même après correction : j'ai arrêté avant la production. Aucun crédit utilisé, l'équipe est prévenue.",
            "نصوص الإعلانات لم تلتزم بحدود الأحرف حتى بعد التصحيح، فتوقفت قبل الإنتاج. لم يُستخدم أي رصيد وتم تنبيه الفريق.",
            "Тексты объявлений не уложились в лимиты знаков даже после правки, поэтому я остановился до производства. Кредиты не потрачены, команда уведомлена."),
        "context_missing": _txt(
            "חסר לי חומר שבלעדיו אני לא מפיק מודעות (ספריית המקצוע או בריף המותג), אז לא הופק כלום. הצוות קיבל התראה.",
            "Material I never produce ads without is missing (the craft library or the brand brief), so nothing was produced. The team was alerted.",
            "Il me manque un élément sans lequel je ne produis pas d'annonces (la bibliothèque métier ou le brief de marque) : rien n'a été produit. L'équipe est prévenue.",
            "ينقصني ما لا أنتج إعلانات بدونه (مكتبة الحرفة أو موجز العلامة)، فلم يُنتج شيء. تم تنبيه الفريق.",
            "Не хватает материала, без которого я не делаю рекламу (библиотека ремесла или бриф бренда), ничего не создано. Команда уведомлена."),
        "stopped": _txt("עצרתי. מה שכבר הופק נשאר בתיקיית ה-Drive שלכם; ההזמנה לא הועברה הלאה.",
                        "Stopped. What was already produced stays in your Drive folder; the order was not handed on.",
                        "Arrêté. Ce qui a déjà été produit reste dans votre Drive ; la commande n'a pas été transmise.",
                        "توقفت. ما أُنتج يبقى في مجلد Drive لديكم؛ لم يُسلَّم الطلب.",
                        "Остановлено. Уже созданное остаётся в вашей папке Drive; заказ дальше не передан."),
    }
    text = {
        "none": _txt("מודעות טקסט בלבד — בלי תמונות", "text ads only — no images", "annonces textuelles uniquement — sans images",
                     "إعلانات نصية فقط — بلا صور", "только текстовые объявления — без изображений"),
        "images": _txt("{n} תמונות ({concepts} רעיונות שונים, כל אחד בכל היחסים שהקמפיין צריך) ולוגו מהקובץ שלכם",
                       "{n} images ({concepts} different ideas, each in every ratio the campaign needs) and your logo from your own file",
                       "{n} images ({concepts} idées différentes, chacune dans tous les formats requis) et votre logo depuis votre fichier",
                       "{n} صور ({concepts} أفكار مختلفة، كل منها بكل النِّسب المطلوبة) وشعاركم من ملفكم",
                       "{n} изображений ({concepts} разных идей, каждая во всех нужных форматах) и ваш логотип из вашего файла"),
        "video": _txt(" הקמפיין הזה צריך גם סרטון שכבר נמצא בערוץ היוטיוב שלכם — את זה אני לא מפיק כאן, אז הסט לא יושלם בלעדיו.",
                      " This campaign also needs a video that is already on your YouTube channel — I don't produce that here, so the set won't be complete without it.",
                      " Cette campagne exige aussi une vidéo déjà présente sur votre chaîne YouTube — je ne la produis pas ici, le lot ne sera donc pas complet sans elle.",
                      " تحتاج هذه الحملة أيضًا إلى فيديو موجود على قناتكم في يوتيوب — لا أنتجه هنا، فلن تكتمل المجموعة بدونه.",
                      " Этой кампании нужно ещё видео, уже размещённое на вашем YouTube-канале, — здесь я его не делаю, без него набор не будет полным."),
        "ready": _txt(
            "מוכן ✓ הסט לקמפיין שלם ועבר שתי בדיקות נפרדות: {images} תמונות, {logos} לוגו ו-{texts} טקסטים, בתיקיית ה-Drive שלכם (ads). שום דבר לא עלה לגוגל ושום קמפיין לא נפתח — זה השלב הבא, באישור שלכם.",
            "Ready ✓ The campaign set is complete and passed two separate checks: {images} images, {logos} logos and {texts} texts, in your Drive folder (ads). Nothing was uploaded to Google and no campaign was opened — that is the next step, with your approval.",
            "Prêt ✓ Le lot de la campagne est complet et a passé deux contrôles distincts : {images} images, {logos} logos et {texts} textes, dans votre Drive (ads). Rien n'a été envoyé à Google et aucune campagne n'a été ouverte — c'est l'étape suivante, avec votre accord.",
            "جاهز ✓ مجموعة الحملة مكتملة واجتازت فحصين منفصلين: {images} صور و{logos} شعار و{texts} نصوص، في مجلد Drive (ads). لم يُرفع شيء إلى جوجل ولم تُفتح أي حملة — هذه الخطوة التالية بموافقتكم.",
            "Готово ✓ Набор для кампании полный и прошёл две отдельные проверки: {images} изображений, {logos} логотипов и {texts} текстов, в вашей папке Drive (ads). В Google ничего не загружено и кампания не открыта — это следующий шаг, с вашего согласия."),
        "incomplete": _txt(
            "הסט לא שלם, אז לא העברתי אותו הלאה ושום דבר לא עלה לגוגל. מה חסר:\n{lines}\nמה שכן הופק ({made} קבצים) נמצא בתיקיית ה-Drive שלכם. הצוות קיבל התראה.",
            "The set is not complete, so it was not handed on and nothing was uploaded to Google. What is missing:\n{lines}\nWhat was produced ({made} files) is in your Drive folder. The team was alerted.",
            "Le lot n'est pas complet : il n'a pas été transmis et rien n'a été envoyé à Google. Ce qui manque :\n{lines}\nCe qui a été produit ({made} fichiers) est dans votre Drive. L'équipe est prévenue.",
            "المجموعة غير مكتملة، لذلك لم تُسلَّم ولم يُرفع شيء إلى جوجل. ما ينقص:\n{lines}\nما أُنتج ({made} ملفات) موجود في مجلد Drive. تم تنبيه الفريق.",
            "Набор неполный, поэтому он не передан дальше и в Google ничего не загружено. Чего не хватает:\n{lines}\nСозданное ({made} файлов) лежит в вашей папке Drive. Команда уведомлена."),
        "rejected": _txt(
            "הסט הועבר לבדיקה, אבל הוא לא עומד במינימום שגוגל דורש, אז שום דבר לא עלה:\n{lines}\nהצוות קיבל התראה.",
            "The set was handed over for checking, but it does not meet the minimum Google requires, so nothing was uploaded:\n{lines}\nThe team was alerted.",
            "Le lot a été transmis pour contrôle, mais il n'atteint pas le minimum exigé par Google : rien n'a été envoyé :\n{lines}\nL'équipe est prévenue.",
            "سُلّمت المجموعة للفحص لكنها لا تستوفي الحد الأدنى الذي تطلبه جوجل، فلم يُرفع شيء:\n{lines}\nتم تنبيه الفريق.",
            "Набор передан на проверку, но не дотягивает до минимума Google, поэтому ничего не загружено:\n{lines}\nКоманда уведомлена."),
        "handed": _txt("קיבלתי מליאור סט שלם לקמפיין \"{offer}\" ובדקתי אותו מול המינימום של גוגל — הוא עומד בו. שום דבר לא עלה עדיין.",
                       "I received a complete set from Lior for the \"{offer}\" campaign and checked it against Google's minimum — it meets it. Nothing has been uploaded yet.",
                       "J'ai reçu de Lior un lot complet pour la campagne « {offer} » et l'ai vérifié par rapport au minimum de Google — il le respecte. Rien n'a encore été envoyé.",
                       "استلمت من ليئور مجموعة مكتملة لحملة \"{offer}\" وفحصتها مقابل الحد الأدنى لجوجل — وهي تستوفيه. لم يُرفع شيء بعد.",
                       "Я получил от Лиора полный набор для кампании «{offer}» и сверил его с минимумом Google — он проходит. Пока ничего не загружено."),
    }

    def v_type(value, client_id):
        value = str(value or "").strip().lower()
        if value not in gaf.CAMPAIGN_TYPES:
            raise ValueError("unknown campaign type")
        return value

    def v_objective(value, client_id):
        value = str(value or "").strip().lower()
        if value not in media_order.OBJECTIVES:
            raise ValueError("unknown objective")
        return value

    def v_url(value, client_id):
        value = str(value or "").strip()
        if not media_order.URL_RE.match(value):
            raise ValueError("not an https address")
        return value[:500]

    def v_date(value, client_id):
        from datetime import date
        return date.fromisoformat(str(value or "").strip()).isoformat()

    def v_text(limit):
        return lambda value, client_id: aa._v_short_text(value, limit)

    def possible(state, client):
        return aa._in_package(state, "google_ads") is not False

    def _order(client_id, params):
        return media_order.issue(client_id, params)

    def prepare(client_id, params, context, rows):
        from core import client_state, post_brief
        lang = (context or {}).get("language") or "he"
        if aa._in_package({"package": client_state.cached_package(client_id)}, "google_ads") is False:
            return {"refuse": _pick(refusals["not_in_package"], lang)}
        missing = post_brief.try_context(client_id).get("missing")
        if missing:
            aa._alert(f"client {client_id}: content context missing ({', '.join(missing)}) — {ACTION} refused")
            return {"refuse": _pick(refusals["context_missing"], lang)}
        try:
            decided = decide(client_id, params, lang)
        except Exception as e:
            aa._alert(f"client {client_id}: the campaign decision failed ({type(e).__name__})")
            return {"refuse": _pick(refusals["decision_failed"], lang)}
        clean = {k: v for k, v in decided.items() if v}
        if params.get("deadline"):
            clean["deadline"] = params["deadline"]
        try:
            order = _order(client_id, clean)
        except media_order.OrderError as e:
            if e.code in ask:
                return {"clarify": _pick(ask[e.code], lang)}
            aa._alert(f"client {client_id}: the campaign order could not be issued ({e})")
            return {"refuse": _pick(refusals["decision_failed"], lang)}
        if media_order.generations(order) and aa._media_tool_status(client_id) not in ("connected",
                                                                                      "connected_unverified"):
            return {"switch_to": "connect_creative_tool", "switch_params": {"tool": "higgsfield"},
                    "notice": _pick(aa._PRODUCE_TOOL_FIRST, lang).replace("{tool}", aa._creative_label("higgsfield", lang))}
        for row in rows:
            if row.get("status") == "proposed" and (row.get("params") or {}) == clean:
                return {"existing": row}
        return {"params": clean, "supersedes": rows[0].get("id") if rows else None}

    def display(client_id, params):
        lang = aa._lang(client_id)
        campaign_type = params.get("campaign_type", "")
        try:
            concepts = max(1, min(media_order.DEFAULT_CONCEPTS, gaf.max_concepts(campaign_type) or 1))
            rows = gaf.required_assets(campaign_type, concepts=concepts)
        except Exception:
            rows, concepts = [], 0
        n = sum(r["count"] for r in rows if r["kind"] == "image")
        produces = (_pick(text["images"], lang).replace("{n}", str(n)).replace("{concepts}", str(concepts))
                    if n else _pick(text["none"], lang))
        if any(r["kind"] == "video" and r["need"] == "required" for r in rows):
            produces += _pick(text["video"], lang)
        return {"type": _pick(types.get(campaign_type) or {"he": campaign_type}, lang),
                "objective": _pick(objectives.get(params.get("objective")) or {"he": ""}, lang),
                "offer": params.get("offer", ""), "audience": params.get("audience_segment", ""),
                "url": params.get("landing_page_url", ""), "produces": produces, "why": params.get("why", ""),
                "generations": str(n)}

    def run(client_id, params, background_tasks, row=None):
        from core import governance, post_brief
        lang = (row or {}).get("language") or aa._lang(client_id)
        proposal_id = (row or {}).get("id")
        try:
            order = _order(client_id, params)
        except media_order.OrderError as e:
            raise ActionFailed("decision_failed", f"{ACTION}: {e}")
        order["status"] = "in_production"
        media_order.save(client_id, order)
        try:
            context = post_brief.load_context(client_id)
        except post_brief.ContextMissing as e:
            raise ActionFailed("context_missing", f"{ACTION}: {e}")
        name = (aa._client(client_id) or {}).get("business_name") or ""
        try:
            with governance.lane_scope("client"):
                written = ad_copy.write(client_id, media_order.copy_brief(order), business_name=name,
                                        context=context, lang=lang)
        except ad_copy.CopyFailed as e:
            aa._alert(f"client {client_id}: media order {order['id']} — ad copy did not fit ({'; '.join(e.lines)[:300]})")
            raise ActionFailed("copy_failed", f"{ACTION}: {e}")
        order["texts"] = written["texts"]
        media_order.save(client_id, order)
        try:
            with governance.lane_scope("client"):
                delivered, files = paid_media_production.produce(
                    client_id, media_order.media_brief(order),
                    stopped=lambda: bool(proposal_id) and aa._asked_to_stop(proposal_id))
        except paid_media_production.ProductionBlocked as e:
            raise ActionFailed(e.code if e.code in ("missing_tool", "context_missing", "stopped") else "provider_error",
                               f"{ACTION}: {e}")
        made = len(delivered["assets"])
        try:
            media_order.hand_over(client_id, order, delivered, files)
        except media_order.IncompleteDelivery as e:
            aa._alert(f"client {client_id}: media order {order['id']} NOT handed over — " +
                      "; ".join(media_order.problem_lines(e.problems, "en"))[:600])
            return {"status": "incomplete", "order": order["id"], "made": made, "problems": e.problems}
        verdict = media_order.yoav_verify(client_id, order, files, order["texts"])
        if not verdict["can_serve"]:
            aa._alert(f"client {client_id}: media order {order['id']} rejected at the platform minimum — " +
                      "; ".join(media_order.problem_lines(verdict["problems"], "en"))[:600])
            return {"status": "rejected", "order": order["id"], "made": made, "problems": verdict["problems"]}
        aa._post_client_message(client_id, "google", _pick(text["handed"], lang).replace("{offer}", order["offer"][:80]))
        counted = verdict["counted"]
        return {"status": "verified", "order": order["id"], "made": made, "images": counted.get("image", 0),
                "logos": counted.get("logo", 0),
                "texts": sum(len(v) for v in order["texts"].values())}

    def done_message(result, lang):
        status = result.get("status")
        if status in ("incomplete", "rejected"):
            lines = "\n".join("• " + line for line in media_order.problem_lines(result.get("problems"), lang))
            return (_pick(text[status], lang).replace("{lines}", lines).replace("{made}", str(result.get("made", 0))))
        out = _pick(text["ready"], lang)
        for k in ("images", "logos", "texts"):
            out = out.replace("{" + k + "}", str(result.get(k, 0)))
        return out

    return {
        ACTION: {
            "approver": "client", "client_requestable": True, "kind": "execute", "needs_row": True,
            # Internal accounts only until one live run (never run against the image tool or a real brief) —
            # opening it to paying clients is the owner's decision.
            "internal_only": True,
            "personas": ("general", "google", "media"),
            "does": "PAID CAMPAIGN, the whole chain in ONE card: when the client asks to prepare / build / launch a "
                    "Google campaign (\"תכינו קמפיין\"), propose this — with whatever they already said (what is "
                    "advertised, the page, the audience) and nothing invented. The Google Ads specialist decides the "
                    "campaign type and objective; on approval the ad texts are written to Google's character limits, "
                    "the media agent produces the full image set in every required ratio, the set is checked from "
                    "the files, and only a COMPLETE set is handed to the Google Ads specialist, who verifies it "
                    "again. Nothing is uploaded to Google Ads and no campaign is opened by this. Never ask the client "
                    "which sizes, ratios or campaign type they want.",
            "params": {
                "request": {"required": False, "validate": v_text(600),
                            "hint": "the client's own words about the campaign, in their language"},
                "campaign_type": {"required": False, "validate": v_type,
                                  "hint": "ONLY when the client named one: " + " | ".join(gaf.CAMPAIGN_TYPES)},
                "objective": {"required": False, "validate": v_objective,
                              "hint": "ONLY when the client said it: " + " | ".join(media_order.OBJECTIVES)},
                "audience_segment": {"required": False, "validate": v_text(200),
                                     "hint": "who it is for, ONLY when the client said it"},
                "landing_page_url": {"required": False, "validate": v_url,
                                     "hint": "an https address the CLIENT gave or that is in account_state; never a guess"},
                "offer": {"required": False, "validate": v_text(400),
                          "hint": "what is advertised, in the client's words"},
                "deadline": {"required": False, "validate": v_date, "hint": "YYYY-MM-DD, ONLY when the client gave a date"},
                "why": {"required": False, "validate": v_text(300), "hint": "leave empty — filled by the system"},
            },
            "available": possible,
            "prepare": prepare,
            "validate_stored": lambda client_id, params: aa.validate_params(aa.ACTIONS[ACTION], params, client_id),
            "run": run,
            "display": display,
            "slow": {"minutes": 25, "eta": _txt(
                "הטקסטים נכתבים תוך דקה, וכל תמונה לוקחת בדרך כלל דקה-שתיים.",
                "The texts are written within a minute, and each image usually takes a minute or two.",
                "Les textes sont écrits en une minute, et chaque image prend généralement une à deux minutes.",
                "تُكتب النصوص خلال دقيقة، وكل صورة تستغرق عادة دقيقة أو دقيقتين.",
                "Тексты пишутся за минуту, каждое изображение обычно занимает минуту-две.")},
            "title": _txt("הכנת קמפיין בגוגל", "Preparing a Google campaign", "Préparer une campagne Google",
                          "تجهيز حملة على جوجل", "Подготовка кампании в Google"),
            "what": _txt(
                "יואב קבע: {type}, למטרת {objective}.\nמה מפרסמים: {offer}\nלמי: {audience}\nלאן המודעות מובילות: {url}\n{why}\nמה יופק: {produces}, וטקסטים למודעות לפי מגבלות התווים של גוגל.\nהתמונות נוצרות בכלי התמונות שבחשבון שלכם (עד {generations} יצירות) ונשמרות בתיקיית ה-Drive. שום דבר לא עולה לגוגל ושום קמפיין לא נפתח בלי אישור נפרד.",
                "Yoav decided: {type}, for {objective}.\nWhat is advertised: {offer}\nFor whom: {audience}\nWhere the ads lead: {url}\n{why}\nWhat will be made: {produces}, and ad texts within Google's character limits.\nThe images are made on the image tool on your own account (up to {generations} generations) and saved to your Drive folder. Nothing is uploaded to Google and no campaign is opened without a separate approval.",
                "Yoav a décidé : {type}, objectif {objective}.\nCe qui est mis en avant : {offer}\nPour qui : {audience}\nOù mènent les annonces : {url}\n{why}\nCe qui sera produit : {produces}, et des textes d'annonces dans les limites de caractères de Google.\nLes images sont créées avec l'outil d'images de votre propre compte (jusqu'à {generations} générations) et enregistrées dans votre Drive. Rien n'est envoyé à Google et aucune campagne n'est ouverte sans validation distincte.",
                "قرّر يوآف: {type}، بهدف {objective}.\nما الذي نعلن عنه: {offer}\nلمن: {audience}\nإلى أين تقود الإعلانات: {url}\n{why}\nما الذي سيُنتج: {produces}، ونصوص إعلانية ضمن حدود الأحرف لدى جوجل.\nتُنشأ الصور بأداة الصور في حسابكم (حتى {generations} عملية إنشاء) وتُحفظ في مجلد Drive. لا يُرفع شيء إلى جوجل ولا تُفتح حملة دون موافقة منفصلة.",
                "Йоав решил: {type}, цель — {objective}.\nЧто рекламируем: {offer}\nДля кого: {audience}\nКуда ведут объявления: {url}\n{why}\nЧто будет сделано: {produces}, а также тексты объявлений в лимитах знаков Google.\nИзображения создаются инструментом изображений в вашем аккаунте (до {generations} генераций) и сохраняются в вашу папку Drive. В Google ничего не загружается и кампания не открывается без отдельного одобрения."),
            "done": text["ready"],
            "done_message": done_message,
            "refusals": {**refusals, "missing_tool": aa._CREATIVE_REFUSALS["missing_tool"]},
        },
    }
