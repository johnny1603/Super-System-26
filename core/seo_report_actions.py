"""The organic SEO report's cards (core/seo_report) — registered into
agent_actions.ACTIONS.

- `build_seo_report`    the client's click builds the report. FREE: part of
                        the organic service. A client whose package does not
                        cover organic never reaches the build — `prepare`
                        answers with the upgrade card instead.
- `upgrade_to_organic`  ONE approval card that explains what the organic
                        service includes; the click prepares the priced
                        upgrade proposal through the existing pricing brain
                        (support_agent._build_upgrade_proposal). Nothing is
                        charged by the click, and there is no one-off payment
                        for a report anywhere.
"""
from core import seo_report

LANGS = ("he", "en", "fr", "ar", "ru")
WHERE = {"he": "לשונית ״ביצועים״, בתיבה ״דוח קידום אורגני״", "en": "the Performance tab, in the \"Organic SEO report\" box",
         "fr": "l'onglet Performances, dans l'encadré « Rapport de référencement naturel »",
         "ar": "تبويب \"الأداء\"، في مربع \"تقرير تحسين محركات البحث\"",
         "ru": "вкладка «Результаты», блок «Отчёт по органическому продвижению»"}


def build(aa) -> dict:
    _txt, ActionFailed = aa._txt, aa.ActionFailed

    def _pick(texts, lang):
        return aa._pick(texts, lang)

    refusals = {
        "package_unreadable": _txt(
            "כרגע אני לא מצליח לראות מה כלול בחבילה שלכם, אז לא התחלתי. נסו שוב בעוד כמה דקות.",
            "I can't see what your package includes right now, so I didn't start. Please try again in a few minutes.",
            "Je ne parviens pas à voir ce que comprend votre formule pour l'instant ; je n'ai donc rien lancé. Réessayez dans quelques minutes.",
            "لا أستطيع الآن رؤية ما تتضمنه باقتكم، لذلك لم أبدأ. حاولوا مجددًا بعد دقائق.",
            "Сейчас я не вижу, что входит в ваш пакет, поэтому не начал. Попробуйте через несколько минут."),
        "not_covered": _txt(
            "הדוח הוא חלק משירות הקידום האורגני, והשירות הזה לא כלול כרגע בחבילה שלכם, אז לא התחלתי.",
            "The report is part of the organic SEO service, and that service isn't in your package right now, so I didn't start.",
            "Le rapport fait partie du service de référencement naturel, qui n'est pas dans votre formule pour l'instant ; je n'ai donc rien lancé.",
            "التقرير جزء من خدمة التحسين المجاني لمحركات البحث، وهي غير مشمولة في باقتكم حاليًا، لذلك لم أبدأ.",
            "Отчёт входит в услугу органического продвижения, а её сейчас нет в вашем пакете, поэтому я не начал."),
        "no_site": _txt(
            "כדי לכתוב את הדוח אני צריך שהאתר שלכם יהיה מחובר. בכרטיס שמתחת מחברים אותו, ואז נמשיך.",
            "To write the report I need your website connected. The card below connects it, and then we continue.",
            "Pour rédiger le rapport, votre site doit être connecté. La carte ci-dessous le connecte, puis nous continuons.",
            "لكتابة التقرير أحتاج أن يكون موقعكم مربوطًا. البطاقة أدناه تربطه ثم نكمل.",
            "Чтобы написать отчёт, нужен подключённый сайт. Карточка ниже подключит его, и мы продолжим."),
        "writing_failed": _txt(
            "כתיבת הדוח לא הצליחה הפעם, והצוות קיבל התראה. כתבו לי \"נסה שוב\" ואריץ מחדש.",
            "Writing the report didn't work this time, and the team was alerted. Write \"try again\" and I'll rerun it.",
            "La rédaction du rapport n'a pas abouti cette fois ; l'équipe est prévenue. Écrivez « réessaie » et je relance.",
            "لم تنجح كتابة التقرير هذه المرة وتم تنبيه الفريق. اكتبوا \"حاول مجددًا\" وسأعيد التنفيذ.",
            "В этот раз отчёт не написался, команда уведомлена. Напишите «ещё раз» — запущу снова."),
    }
    fresh = _txt(
        "הדוח שלכם מ-{date} עדיין טרי, אז לא כתבתי חדש. הוא מחכה לכם כאן: {where}.",
        "Your report from {date} is still fresh, so I didn't write a new one. It's waiting for you here: {where}.",
        "Votre rapport du {date} est encore récent ; je n'en ai pas rédigé de nouveau. Il vous attend ici : {where}.",
        "تقريركم بتاريخ {date} ما زال حديثًا، لذلك لم أكتب تقريرًا جديدًا. تجدونه هنا: {where}.",
        "Ваш отчёт от {date} ещё свежий, новый я не писал. Он здесь: {where}.")
    upgrade_notice = _txt(
        "הדוח הוא חלק משירות הקידום האורגני ולא נמכר בנפרד. השירות לא כלול כרגע בחבילה שלכם, אז הנה מה שהוא כולל:",
        "The report is part of the organic SEO service and is never sold separately. The service isn't in your package right now, so here is what it includes:",
        "Le rapport fait partie du service de référencement naturel et n'est jamais vendu séparément. Ce service n'est pas dans votre formule pour l'instant ; voici ce qu'il comprend :",
        "التقرير جزء من خدمة التحسين المجاني لمحركات البحث ولا يُباع منفردًا. الخدمة غير مشمولة في باقتكم حاليًا، وهذا ما تتضمنه:",
        "Отчёт входит в услугу органического продвижения и отдельно не продаётся. Сейчас услуги нет в вашем пакете — вот что она включает:")
    ready = _txt(
        "הדוח מוכן 📄 הוא מחכה לכם כאן: {where}. הוא חלק מהשירות, בלי תשלום נוסף. מצאתי {count} הזדמנויות לקישורים, ולכל אחת יש טיוטת פנייה. שום דבר לא נשלח בלי אישור שלכם.",
        "The report is ready 📄 It's waiting for you here: {where}. It is part of your service, at no extra charge. I found {count} backlink opportunities, each with an outreach draft. Nothing is sent without your approval.",
        "Le rapport est prêt 📄 Il vous attend ici : {where}. Il fait partie de votre service, sans supplément. J'ai trouvé {count} opportunités de liens, chacune avec un brouillon de message. Rien n'est envoyé sans votre accord.",
        "التقرير جاهز 📄 تجدونه هنا: {where}. هو جزء من خدمتكم دون أي رسوم إضافية. وجدت {count} فرصًا للروابط، ولكل منها مسودة رسالة. لا يُرسل شيء دون موافقتكم.",
        "Отчёт готов 📄 Он здесь: {where}. Он входит в вашу услугу, без доплаты. Найдено возможностей для ссылок: {count}, к каждой есть черновик письма. Ничего не отправляется без вашего одобрения.")

    def v_focus(value, client_id):
        return aa._v_short_text(value, 300)

    def prepare_report(client_id, params, context, rows):
        lang = (context or {}).get("language") or "he"
        cover = seo_report.coverage(client_id)
        if not cover["readable"]:
            return {"refuse": _pick(refusals["package_unreadable"], lang)}
        if not cover["covered"]:
            # Never a report (or its characterisation) before the organic service: a SMALL taste
            # (kinds of places, code-built — no names, no drafts) and the ONE upgrade card
            return {"switch_to": "upgrade_to_organic", "switch_params": {},
                    "notice": seo_report.taste(lang) + "\n\n" + _pick(upgrade_notice, lang)}
        stored = seo_report.latest(client_id)
        if stored and seo_report.fresh_enough(stored):
            return {"refuse": _pick(fresh, lang).replace("{date}", str(stored.get("built_at"))[:10])
                    .replace("{where}", WHERE.get(lang, WHERE["en"]))}
        clean = {"focus": params["focus"]} if params.get("focus") else {}
        if not clean:
            # The agreed 3-month keywords are the report's focus unless the client asked for another
            try:
                from core import quarter_goals
                words = (quarter_goals.agreed(client_id) or {}).get("keywords") or []
                if words:
                    clean = {"focus": ", ".join(words)[:300]}
            except Exception:
                pass
        if rows:
            return {"existing": rows[0]}
        return {"params": clean}

    def validate_report(client_id, params):
        params = params if isinstance(params, dict) else {}
        if set(params) - {"focus"}:
            raise ValueError("unknown params")
        return {"focus": v_focus(params["focus"], client_id)} if params.get("focus") else {}

    def run_report(client_id, params, background_tasks, row=None):
        try:
            value = seo_report.build(client_id, params.get("focus", ""), lang=(row or {}).get("language") or "he")
        except seo_report.ReportError as e:
            if e.code == "writing_failed":
                aa._alert(f"client {client_id}: the organic SEO report could not be written ({str(e)[:200]})")
            raise ActionFailed(e.code if e.code in refusals else "error", str(e))
        return {"count": len(value["report"]["backlink_opportunities"]), "language": value.get("language", "he")}

    def organic_missing(state, client):
        package = (state or {}).get("package") or {}
        return bool(package.get("readable")) and not seo_report.covered(package.get("services") or [])

    def organic_price(client_id, params):
        try:
            from agents.onboarding_agent import PRICING
            return {"price": PRICING["organic_seo_monthly"]}
        except Exception:
            return {"price": ""}

    def run_upgrade(client_id, params, background_tasks):
        if seo_report.coverage(client_id)["covered"]:
            raise ActionFailed("already_covered", "organic is already in the package")
        from agents import support_agent
        client = aa._client(client_id)
        try:
            reply = support_agent._build_upgrade_proposal(
                client_id, client, support_agent._latest_lead(client_id),
                "Add the organic SEO service (fixed monthly price) to my current package.")
        except Exception as e:
            aa._alert(f"client {client_id}: asked to add organic SEO and the upgrade proposal failed to build "
                      f"({type(e).__name__}) — send them the offer by hand")
            raise ActionFailed("proposal_failed", f"{type(e).__name__}: {e}")
        return {"reply": str(reply or "")}

    upgraded = _txt("הכנתי את הצעת השדרוג. היא כאן למטה.", "I prepared the upgrade offer. It is right below.",
                    "J'ai préparé l'offre de mise à niveau. Elle est juste en dessous.", "جهّزت عرض الترقية. تجدونه أدناه.",
                    "Я подготовил предложение по расширению. Оно ниже.")

    return {
        "build_seo_report": {
            "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
            "needs_row": True,
            "does": "ORGANIC SEO REPORT: writes the client's professional organic SEO report — where the site stands, "
                    "keyword opportunities, and backlink opportunities matched to their industry, each with a quality "
                    "score, why it fits, what to do and a ready outreach draft. FREE, part of the organic service; "
                    "never present it as a paid deliverable. If their package does not cover organic the system shows "
                    "the upgrade card instead — do not ask them report questions first.",
            "params": {"focus": {"required": False, "validate": v_focus,
                                 "hint": "optional, the client's language: what they most want to be found for, from "
                                         "what they agreed to in this chat"}},
            "available": lambda state, client: True,
            "prepare": prepare_report,
            "validate_stored": validate_report,
            "run": run_report,
            "follow_up": {"no_site": "connect_website"},
            "slow": {"minutes": 6, "eta": _txt(
                "דוח כזה לוקח בדרך כלל שלוש-ארבע דקות.", "A report like this usually takes three to four minutes.",
                "Un tel rapport prend en général trois à quatre minutes.", "تقرير كهذا يستغرق عادةً ثلاث إلى أربع دقائق.",
                "Такой отчёт обычно занимает три-четыре минуты.")},
            "title": _txt("דוח קידום אורגני", "Organic SEO report", "Rapport de référencement naturel",
                          "تقرير تحسين محركات البحث", "Отчёт по органическому продвижению"),
            "what": _txt(
                "אכתוב לכם דוח קידום אורגני מקצועי: איפה האתר עומד היום, הזדמנויות במילות מפתח, ורשימת הזדמנויות לקישורים נכנסים שמתאימות לתחום שלכם, עם ציון איכות, מה לעשות וטיוטת פנייה מוכנה לכל אחת. הדוח הוא חלק מהשירות שלכם ואינו כרוך בתשלום. שום פנייה לא נשלחת בלי אישור שלכם, ואף אחד לא יכול להבטיח מיקום בגוגל.",
                "I'll write your professional organic SEO report: where the site stands today, keyword opportunities, and a list of backlink opportunities that fit your field — each with a quality score, what to do and a ready outreach draft. The report is part of your service and costs nothing. No message is sent without your approval, and no one can promise a position on Google.",
                "Je rédige votre rapport professionnel de référencement naturel : l'état actuel du site, des opportunités de mots-clés et une liste d'opportunités de liens adaptées à votre secteur, chacune avec un score de qualité, l'action à mener et un brouillon de message. Le rapport fait partie de votre service et ne coûte rien. Aucun message n'est envoyé sans votre accord, et personne ne peut promettre une position sur Google.",
                "سأكتب لكم تقريرًا مهنيًا لتحسين محركات البحث: أين يقف الموقع اليوم، فرص الكلمات المفتاحية، وقائمة بفرص الروابط الواردة المناسبة لمجالكم، مع درجة جودة وما المطلوب ومسودة رسالة لكل فرصة. التقرير جزء من خدمتكم دون أي تكلفة. لا تُرسل أي رسالة دون موافقتكم، ولا أحد يستطيع أن يعد بترتيب في غوغل.",
                "Я напишу профессиональный отчёт по органическому продвижению: текущее состояние сайта, возможности по ключевым словам и список подходящих вашей сфере возможностей для входящих ссылок — с оценкой качества, шагами и готовым черновиком письма. Отчёт входит в вашу услугу и ничего не стоит. Ни одно письмо не уходит без вашего одобрения, и никто не может обещать позицию в Google."),
            "done": ready,
            "done_message": lambda result, lang: _pick(ready, lang).replace("{count}", str(result.get("count", 0)))
            .replace("{where}", WHERE.get(lang, WHERE["en"])),
            "refusals": refusals,
        },
        "upgrade_to_organic": {
            "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
            "does": "Offers the ORGANIC SEO service to a client whose package does not include it (seo_report."
                    "covered_by_package false): ONE card explaining what the service includes; the click prepares the "
                    "priced upgrade offer. Propose it when they ask for the SEO report, for organic promotion, or to be "
                    "found on Google without ads. The report is never sold on its own.",
            "params": {},
            "available": organic_missing,
            "display": organic_price,
            "run": run_upgrade,
            "slow": {"minutes": 4, "eta": _txt(
                "הכנת ההצעה לוקחת בדרך כלל כדקה.", "Preparing the offer usually takes about a minute.",
                "La préparation de l'offre prend en général une minute environ.", "تجهيز العرض يستغرق عادةً نحو دقيقة.",
                "Подготовка предложения обычно занимает около минуты.")},
            "button": _txt("להכין לי הצעת שדרוג", "Prepare my upgrade offer", "Préparer mon offre", "جهّزوا لي عرض الترقية",
                           "Подготовить предложение"),
            "title": _txt("להוסיף קידום אורגני לחבילה", "Add organic SEO to your package",
                          "Ajouter le référencement naturel à votre formule", "إضافة التحسين المجاني إلى الباقة",
                          "Добавить органическое продвижение в пакет"),
            "what": _txt(
                "שירות הקידום האורגני כולל: אפיון קצר של מה שאתם רוצים שימצאו אתכם בו, בדיקת מצב האתר, מחקר מילות מפתח ומתחרים, דוח מקצועי עם הזדמנויות לקישורים נכנסים וטיוטות פנייה מוכנות, ומאמרים לאתר שנכתבים כטיוטה לאישור שלכם. כלי הקידום המקצועיים כלולים במחיר. המחיר קבוע: ₪{price} לחודש. הדוח עצמו הוא חלק מהשירות ולא נמכר בנפרד. אף אחד לא יכול להבטיח מיקום בגוגל. הלחיצה כאן רק מכינה לכם הצעת שדרוג מסודרת; שום דבר לא מחויב לפני שאישרתם אותה.",
                "The organic SEO service includes: a short characterisation of what you want to be found for, a check of the site's state, keyword and competitor research, a professional report with backlink opportunities and ready outreach drafts, and articles for your site written as drafts for your approval. The professional SEO tools are included in the price. The price is fixed: ₪{price} a month. The report itself is part of the service and is never sold separately. No one can promise a position on Google. This click only prepares an orderly upgrade offer; nothing is charged before you approve it.",
                "Le service de référencement naturel comprend : un court cadrage de ce pour quoi vous voulez être trouvé, un état du site, une recherche de mots-clés et de concurrents, un rapport professionnel avec des opportunités de liens et des brouillons de messages, et des articles rédigés en brouillon pour votre validation. Les outils professionnels sont inclus dans le prix. Le prix est fixe : ₪{price} par mois. Le rapport fait partie du service et n'est jamais vendu séparément. Personne ne peut promettre une position sur Google. Ce clic prépare seulement une offre ; rien n'est facturé avant votre validation.",
                "خدمة التحسين المجاني تشمل: توصيفًا قصيرًا لما تريدون أن يجدكم الناس به، فحص حالة الموقع، بحث الكلمات المفتاحية والمنافسين، تقريرًا مهنيًا بفرص الروابط الواردة ومسودات رسائل جاهزة، ومقالات لموقعكم تُكتب كمسودات لموافقتكم. أدوات التحسين المهنية مشمولة في السعر. السعر ثابت: ₪{price} شهريًا. التقرير نفسه جزء من الخدمة ولا يُباع منفردًا. لا أحد يستطيع أن يعد بترتيب في غوغل. هذه النقرة تجهّز عرض ترقية فقط؛ لا يُخصم شيء قبل موافقتكم عليه.",
                "Услуга органического продвижения включает: короткое уточнение, по каким запросам вас должны находить, проверку состояния сайта, исследование ключевых слов и конкурентов, профессиональный отчёт с возможностями для входящих ссылок и готовыми черновиками писем, а также статьи для сайта в виде черновиков на ваше одобрение. Профессиональные инструменты включены в цену. Цена фиксированная: ₪{price} в месяц. Сам отчёт входит в услугу и отдельно не продаётся. Никто не может обещать позицию в Google. Это нажатие только готовит предложение; ничего не списывается до вашего одобрения."),
            "done": upgraded,
            "done_message": lambda result, lang: result.get("reply") or _pick(upgraded, lang),
            "refusals": {
                "already_covered": _txt(
                    "הקידום האורגני כבר כלול בחבילה שלכם, אז אין מה לשדרג. אפשר פשוט לבקש ממני את הדוח.",
                    "Organic SEO is already in your package, so there is nothing to upgrade. Just ask me for the report.",
                    "Le référencement naturel est déjà dans votre formule : rien à ajouter. Demandez-moi simplement le rapport.",
                    "التحسين المجاني مشمول بالفعل في باقتكم، فلا حاجة للترقية. اطلبوا مني التقرير فقط.",
                    "Органическое продвижение уже в вашем пакете, расширять нечего. Просто попросите у меня отчёт."),
                "proposal_failed": _txt(
                    "לא הצלחתי להכין את ההצעה הפעם, והצוות קיבל התראה וישלח לכם אותה.",
                    "I couldn't prepare the offer this time; the team was alerted and will send it to you.",
                    "Je n'ai pas pu préparer l'offre cette fois ; l'équipe est prévenue et vous l'enverra.",
                    "لم أتمكن من تجهيز العرض هذه المرة؛ تم تنبيه الفريق وسيرسلونه إليكم.",
                    "В этот раз предложение не подготовилось; команда уведомлена и пришлёт его вам."),
            },
        },
    }
