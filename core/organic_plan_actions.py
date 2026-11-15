"""The organic plan's cards (core/organic_plan) — registered into agent_actions.ACTIONS.

Two client decisions, nothing else to click:
- `advance_organic_plan`      runs the automatic steps up to the next decision:
                              the state report, then the keyword research. FREE
                              (part of the organic service); a package without
                              organic is answered with the upgrade card.
- `approve_organic_keywords`  the client approves a small set (1-5) of the
                              RESEARCHED candidates; the same click runs the
                              backlink check and the strategy with its time
                              estimate. Then the 3-month goals may be agreed.
"""
from core import organic_plan

LANGS = ("he", "en", "fr", "ar", "ru")


def build(aa) -> dict:
    _txt, ActionFailed = aa._txt, aa.ActionFailed

    def _pick(texts, lang):
        return aa._pick(texts, lang)

    refusals = {
        "package_unreadable": _txt(
            "כרגע אני לא מצליח לראות מה כלול בחבילה שלכם, אז לא התחלתי. נסו שוב בעוד כמה דקות.",
            "I can't see what your package includes right now, so I didn't start. Please try again in a few minutes.",
            "Je ne vois pas ce que comprend votre formule pour l'instant ; je n'ai rien lancé. Réessayez dans quelques minutes.",
            "لا أستطيع الآن رؤية ما تتضمنه باقتكم، لذلك لم أبدأ. حاولوا بعد دقائق.",
            "Сейчас я не вижу, что входит в ваш пакет, поэтому не начал. Попробуйте через несколько минут."),
        "no_site": _txt(
            "כדי לבדוק את מצב האתר הוא צריך להיות מחובר. בכרטיס שמתחת מחברים אותו, ואז נמשיך.",
            "To check the site's state it needs to be connected. The card below connects it, and then we continue.",
            "Pour analyser le site, il doit être connecté. La carte ci-dessous le connecte, puis nous continuons.",
            "لفحص حالة الموقع يجب أن يكون مربوطًا. البطاقة أدناه تربطه ثم نكمل.",
            "Чтобы проверить сайт, его нужно подключить. Карточка ниже подключит его, и мы продолжим."),
        "error": _txt(
            "השלב הזה לא הסתיים הפעם, ואני כבר מטפל בזה. כתבו לי \"נסה שוב\" ואריץ אותו מחדש.",
            "This step didn't finish this time, and I'm already on it. Write \"try again\" and I'll rerun it.",
            "Cette étape n'a pas abouti cette fois ; je m'en occupe. Écrivez « réessaie » et je la relance.",
            "لم تكتمل هذه الخطوة هذه المرة وأنا أتابعها. اكتبوا \"حاول مجددًا\" وسأعيدها.",
            "Этот шаг в этот раз не завершился, я уже разбираюсь. Напишите «ещё раз» — запущу снова."),
    }
    choose_first = _txt(
        "המחקר מוכן, ועכשיו ההחלטה שלכם: איזה ביטויים (עד 5) נבחר לעבוד ולהימדד עליהם?\n{lines}\n{basis}",
        "The research is ready, and now it's your call: which phrases (up to 5) do we work on and measure?\n{lines}\n{basis}",
        "La recherche est prête ; à vous de choisir : quelles expressions (5 au plus) travaillons-nous et mesurons-nous ?\n{lines}\n{basis}",
        "البحث جاهز، والآن القرار لكم: أي عبارات (حتى 5) نعمل عليها ونقيسها؟\n{lines}\n{basis}",
        "Исследование готово, теперь решаете вы: какие фразы (до 5) продвигаем и измеряем?\n{lines}\n{basis}")
    already_ready = _txt(
        "התוכנית האורגנית כבר מוכנה. השלב הבא הוא לסכם את היעדים ל-3 החודשים.",
        "The organic plan is already ready. The next step is agreeing the 3-month goals.",
        "Le plan organique est déjà prêt. Prochaine étape : convenir des objectifs à 3 mois.",
        "الخطة العضوية جاهزة بالفعل. الخطوة التالية الاتفاق على أهداف الأشهر الثلاثة.",
        "Органический план уже готов. Следующий шаг — согласовать цели на 3 месяца.")
    advanced = _txt("{next}", "{next}", "{next}", "{next}", "{next}")
    strategy_done = _txt(
        "התוכנית מוכנה ✓ זה מה שנעשה, לפי הסדר:\n{steps}\n{estimate}\nהשלב הבא: לסכם יחד את היעדים ל-3 החודשים.",
        "The plan is ready ✓ This is what we'll do, in order:\n{steps}\n{estimate}\nNext: agreeing the 3-month goals together.",
        "Le plan est prêt ✓ Voici ce que nous ferons, dans l'ordre :\n{steps}\n{estimate}\nEnsuite : convenir ensemble des objectifs à 3 mois.",
        "الخطة جاهزة ✓ هذا ما سنفعله بالترتيب:\n{steps}\n{estimate}\nالتالي: نتفق معًا على أهداف الأشهر الثلاثة.",
        "План готов ✓ Вот что мы сделаем, по порядку:\n{steps}\n{estimate}\nДальше: вместе согласуем цели на 3 месяца.")

    def coverage_answer(client_id, lang):
        from core import seo_report
        cover = seo_report.coverage(client_id)
        if not cover["readable"]:
            return {"refuse": _pick(refusals["package_unreadable"], lang)}
        if not cover["covered"]:
            return {"switch_to": "upgrade_to_organic", "switch_params": {}}
        return None

    def prepare_advance(client_id, params, context, rows):
        lang = (context or {}).get("language") or "he"
        blocked = coverage_answer(client_id, lang)
        if blocked:
            return blocked
        value = organic_plan.current(client_id)
        step = organic_plan.stage(value)
        if step == "approve":
            return {"clarify": choose_line(value, lang)}
        if step == "ready":
            return {"clarify": _pick(already_ready, lang)}
        if rows:
            return {"existing": rows[0]}
        return {"params": {}}

    def choose_line(value, lang):
        research = value.get("research") or {}
        basis = "" if research.get("basis") == "measured" else organic_plan.pick(organic_plan.TEXT["basis_judgment"], lang)
        return (_pick(choose_first, lang).replace("{lines}", organic_plan.candidate_lines(research.get("candidates") or [], lang))
                .replace("{basis}", basis).strip())

    def run_advance(client_id, params, background_tasks, row=None):
        lang = (row or {}).get("language") or "he"
        try:
            value = organic_plan.current(client_id)
            while organic_plan.stage(value) in ("state", "keywords"):
                value = organic_plan.advance(client_id, lang)
        except organic_plan.PlanError as e:
            aa._alert(f"client {client_id}: organic plan step stopped ({e.code})")
            raise ActionFailed("error", e.code)
        except Exception as e:
            from core import seo_report
            if isinstance(e, seo_report.ReportError) and e.code in ("no_site", "package_unreadable"):
                raise ActionFailed(e.code, e.code)
            aa._alert(f"client {client_id}: organic plan step failed ({type(e).__name__}: {str(e)[:200]})")
            raise ActionFailed("error", f"{type(e).__name__}")
        return {"text": choose_line(value, lang)}

    def v_keywords(value, client_id):
        return organic_plan.check_approval(value, (organic_plan.current(client_id).get("research") or {}).get("candidates") or [])

    def prepare_approve(client_id, params, context, rows):
        lang = (context or {}).get("language") or "he"
        blocked = coverage_answer(client_id, lang)
        if blocked:
            return blocked
        value = organic_plan.current(client_id)
        if not value.get("research"):
            # Never a keyword choice before the research
            return {"switch_to": "advance_organic_plan", "switch_params": {},
                    "notice": organic_plan.next_line(client_id, lang)}
        if rows:
            return {"params": params, "supersedes": rows[0].get("id")}
        return {"params": params}

    def display_approve(client_id, params):
        lang = aa._lang(client_id)
        candidates = (organic_plan.current(client_id).get("research") or {}).get("candidates") or []
        chosen = {organic_plan._norm(k) for k in params.get("keywords") or []}
        rows = [c for c in candidates if organic_plan._norm(c["keyword"]) in chosen]
        research = organic_plan.current(client_id).get("research") or {}
        basis = "" if research.get("basis") == "measured" else organic_plan.pick(organic_plan.TEXT["basis_judgment"], lang)
        return {"lines": organic_plan.candidate_lines(rows, lang), "basis": basis}

    def run_approve(client_id, params, background_tasks, row=None):
        lang = (row or {}).get("language") or "he"
        try:
            organic_plan.approve(client_id, params["keywords"])
            organic_plan.check_backlinks(client_id)
            value = organic_plan.build_strategy(client_id, lang)
        except ValueError as e:
            raise ActionFailed("error", str(e))
        except Exception as e:
            aa._alert(f"client {client_id}: organic strategy failed after the keyword approval "
                      f"({type(e).__name__}: {str(e)[:200]})")
            raise ActionFailed("error", type(e).__name__)
        s = value["strategy"]
        steps = "\n".join(f"{i}. {x['what']} — {x['why']}" for i, x in enumerate(s["steps"], 1))
        estimate = organic_plan.pick(organic_plan.TEXT["estimate_line"], lang) \
            .replace("{i}", f"{s['internal_months']:g}").replace("{c}", f"{s['committed_months']:g}")
        return {"text": _pick(strategy_done, lang).replace("{steps}", steps).replace("{estimate}", estimate)}

    slow_eta = _txt("השלב הזה לוקח בדרך כלל כמה דקות.", "This step usually takes a few minutes.",
                    "Cette étape prend en général quelques minutes.", "تستغرق هذه الخطوة عادةً بضع دقائق.",
                    "Этот шаг обычно занимает несколько минут.")
    return {
        "advance_organic_plan": {
            "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
            "needs_row": True,
            "does": "ORGANIC PLAN, step by step (organic_plan.stage): runs the next automatic steps — the site's state "
                    "report (what it shows today, who the competitors are), then the professional keyword research "
                    "(the client's own phrases + Google's related searches + competitor terms, each with intent, volume "
                    "and competition) — and stops at the client's choice. Propose it when the client wants organic "
                    "growth / to be found on Google and organic_plan.stage is state or keywords. NEVER propose a keyword "
                    "in your own words before this research ran.",
            "params": {},
            "available": lambda state, client: True,
            "prepare": prepare_advance,
            "validate_stored": lambda client_id, params: {},
            "run": run_advance,
            "follow_up": {"no_site": "connect_website"},
            "slow": {"minutes": 8, "eta": slow_eta},
            "title": _txt("תוכנית אורגנית: דוח מצב ומחקר ביטויים", "Organic plan: state report and keyword research",
                          "Plan organique : état des lieux et recherche de mots-clés", "الخطة العضوية: تقرير الحالة وبحث الكلمات",
                          "Органический план: отчёт о состоянии и подбор фраз"),
            "what": _txt(
                "אבדוק מה האתר מראה היום ומי המתחרים, ואז אעשה מחקר ביטויים מקצועי: הביטויים שלכם, החיפושים הקשורים בגוגל ומה שהמתחרים מכוונים אליו, עם כוונת החיפוש, נפח ותחרות. בסוף תבחרו סט קטן, ורק עליו נעבוד ונמדוד. זה חלק מהשירות, בלי תשלום נוסף, ואף אחד לא יכול להבטיח מיקום בגוגל.",
                "I'll check what the site shows today and who the competitors are, then do professional keyword research: your phrases, Google's related searches and what competitors target, with search intent, volume and competition. Then you choose a small set, and only that set is worked on and measured. It's part of your service at no extra charge, and no one can promise a position on Google.",
                "J'analyse ce que montre le site aujourd'hui et qui sont les concurrents, puis je fais une recherche de mots-clés professionnelle : vos expressions, les recherches associées de Google et ce que visent les concurrents, avec intention, volume et concurrence. Vous choisissez ensuite un petit ensemble, le seul travaillé et mesuré. C'est inclus dans votre service, et personne ne peut promettre une position sur Google.",
                "سأفحص ما يُظهره الموقع اليوم ومن المنافسون، ثم أجري بحث كلمات احترافيًا: عباراتكم، وعمليات البحث المرتبطة في Google وما يستهدفه المنافسون، مع نية البحث والحجم والمنافسة. ثم تختارون مجموعة صغيرة نعمل عليها ونقيسها وحدها. هذا جزء من خدمتكم دون تكلفة إضافية، ولا أحد يستطيع أن يعد بترتيب في Google.",
                "Я проверю, что показывает сайт сегодня и кто конкуренты, затем проведу профессиональный подбор фраз: ваши фразы, связанные запросы Google и то, на что нацелены конкуренты, с намерением, объёмом и конкуренцией. Потом вы выберете небольшой набор — только его продвигаем и измеряем. Это входит в услугу, без доплаты, и никто не может обещать позицию в Google."),
            "done": advanced,
            "done_message": lambda result, lang: result.get("text") or organic_plan.pick(organic_plan.TEXT["next_approve"], lang),
            "refusals": refusals,
        },
        "approve_organic_keywords": {
            "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
            "needs_row": True,
            "does": "The client's CHOICE of the organic keywords: 1-5 phrases taken ONLY from organic_plan.candidates "
                    "(the research). Only these are measured and worked on, and the goals and the management-fee "
                    "commitment are measured on them. Propose it with exactly the phrases the client picked. The same "
                    "click runs the backlink check and writes the strategy with its time estimate.",
            "params": {"keywords": {"required": True, "validate": v_keywords,
                                    "hint": "comma-separated, 1-5 phrases copied exactly from organic_plan.candidates"}},
            "available": lambda state, client: True,
            "prepare": prepare_approve,
            "validate_stored": lambda client_id, params: {"keywords": v_keywords((params or {}).get("keywords"), client_id)},
            "display": display_approve,
            "run": run_approve,
            "slow": {"minutes": 6, "eta": slow_eta},
            "button": _txt("אלה הביטויים שלנו", "These are our phrases", "Ce sont nos expressions", "هذه عباراتنا",
                           "Это наши фразы"),
            "title": _txt("הביטויים שנעבוד עליהם", "The phrases we'll work on", "Les expressions que nous travaillerons",
                          "العبارات التي سنعمل عليها", "Фразы, которые продвигаем"),
            "what": _txt(
                "אלה הביטויים שבחרתם לעבוד ולהימדד עליהם:\n{lines}\n{basis}\nרק עליהם נמדוד את ההתקדמות ואת היעדים. אחרי האישור אבדוק את הקישורים שלכם ושל המתחרים ואכתוב אסטרטגיה עם הערכת זמן. אף אחד לא יכול להבטיח מיקום בגוגל.",
                "These are the phrases you chose to work on and be measured on:\n{lines}\n{basis}\nOnly these are used to measure progress and the goals. After you approve, I'll check your links and the competitors' and write a strategy with a time estimate. No one can promise a position on Google.",
                "Voici les expressions choisies pour être travaillées et mesurées :\n{lines}\n{basis}\nSeules celles-ci servent à mesurer les progrès et les objectifs. Après validation, j'analyse vos liens et ceux des concurrents et j'écris une stratégie avec une estimation de durée. Personne ne peut promettre une position sur Google.",
                "هذه العبارات التي اخترتم العمل عليها وقياسها:\n{lines}\n{basis}\nعليها فقط نقيس التقدم والأهداف. بعد الموافقة سأفحص روابطكم وروابط المنافسين وأكتب استراتيجية مع تقدير زمني. لا أحد يستطيع أن يعد بترتيب في Google.",
                "Вот фразы, которые вы выбрали для продвижения и измерения:\n{lines}\n{basis}\nТолько по ним измеряем прогресс и цели. После одобрения я проверю ваши ссылки и ссылки конкурентов и напишу стратегию с оценкой сроков. Никто не может обещать позицию в Google."),
            "done": advanced,
            "done_message": lambda result, lang: result.get("text") or organic_plan.pick(organic_plan.TEXT["ready"], lang),
            "refusals": refusals,
        },
    }
