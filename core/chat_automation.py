"""Chat automation: a SALES conversation that ends in ONE "schedule a call" card (2026-11-17).

The service (instant replies to the business's enquiries on its messaging
channels) is built by hand with a person from our team, so there is nothing
for the client to connect. What the client gets instead:

- the manager (ג'וני / ניסים / אלינור) sells it in their own voice and plain
  business words (SALES_RULE, read by support_agent as _AUTOMATION_RULE):
  instant replies at any hour, a responsive impression, leads warmed up and
  qualified before anyone talks to them, less time on irrelevant enquiries,
  examples from the client's own industry, and questions as READY GUESSES;
- transparency: our platform is mainly the bridge, and a team member sits
  with the client on every automation and conversation flow; the cost is a
  one-off setup fee plus an ongoing software subscription chosen for their
  needs, and NO number is ever shown before the human characterisation;
- the ONLY next step is the card `book_automation_call`. Approving it opens a
  tracked team request (topic `automation_call`, agent_actions) for the
  representative. Nothing is built before that call.

Never in this flow: a tool or vendor name, "do you already have an account",
a sign-up link, a credential, a setup step, a connect card, a price. The
vendor stays in the admin-only code (core/automation_service).
tests/test_chat_automation.py.
"""

LANGS = ("he", "en", "fr", "ar", "ru")
CALL_ACTION = "book_automation_call"
TEAM_TOPIC = "automation_call"


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


def pick(table: dict, lang: str) -> str:
    return table.get(lang) or table["he"]


# The code-built pitch (the card's "tell me more" from an old connect button,
# and the fallback when no model turn runs). The model's own pitch follows
# SALES_RULE and adapts it to the client's industry.
PITCH = _five(
    "אוטומציית שיחות עונה לכל פנייה חדשה מיד, בכל שעה. כך ליד לא מחכה ולא הולך למתחרה, והעסק נראה מקצועי וזמין. "
    "היא גם מחממת ומסננת את הפונים: מי רציני, מה הוא צריך ומה התקציב, עוד לפני שמישהו מכם מדבר איתו. "
    "ככה הרבה פחות זמן הולך על פניות לא רלוונטיות.\n"
    "איך זה עובד: המערכת שלנו היא בעיקר הגשר. איש צוות שלנו יושב איתכם אישית על כל האוטומציות ועל מסלולי השיחה "
    "שהלקוחות שלכם יעברו לפני שתיצרו איתם קשר.\n"
    "העלות: דמי הקמה חד-פעמיים, ומנוי שוטף לתוכנה שנבחר לפי הצורך המדויק שלכם. את המחיר נותנים רק אחרי שיחת אפיון עם נציג.\n"
    "ואם בא לכם, ספרו לי כבר עכשיו: הפניות מגיעות אליכם בעיקר בוואטסאפ, באינסטגרם או בטלפון? "
    "ואיפה הכי הרבה פונים נופלים: כשהתשובה מתעכבת, או אחרי שהם שומעים מחיר?\n"
    "הצעד הבא הוא שיחה קצרה עם נציג. מאשרים בכרטיס כאן ומתאמים.",
    "Chat automation answers every new enquiry instantly, at any hour. A lead never waits and doesn't drift to a competitor, "
    "and your business comes across as professional and responsive. It also warms up and qualifies people: who is serious, "
    "what they need and what their budget is, before any of you talks to them. So much less time goes on irrelevant enquiries.\n"
    "How it works: our platform is mainly the bridge. A member of our team sits with you personally on every automation and "
    "on the conversation flows your customers will go through before you contact them.\n"
    "The cost: a one-off setup fee, plus an ongoing subscription to software chosen for your exact needs. The price is given "
    "only after a characterisation call with a representative.\n"
    "If you like, tell me now: do your enquiries come mostly on WhatsApp, Instagram or by phone? And where do you lose the most "
    "people: when the reply is slow, or after they hear a price?\n"
    "The next step is a short call with a representative. Approve it on the card here and we'll arrange it.",
    "L'automatisation des conversations répond tout de suite à chaque nouvelle demande, à toute heure. Un prospect n'attend "
    "jamais et ne part pas chez un concurrent, et votre entreprise paraît professionnelle et disponible. Elle qualifie aussi les "
    "contacts : qui est sérieux, ce qu'il cherche, son budget, avant que vous lui parliez. Beaucoup moins de temps perdu sur des "
    "demandes hors sujet.\n"
    "Comment ça marche : notre plateforme sert surtout de pont. Un membre de notre équipe travaille avec vous personnellement sur "
    "chaque automatisation et sur les parcours de conversation que vos clients suivront avant que vous les contactiez.\n"
    "Le coût : des frais de mise en place uniques, plus un abonnement à un logiciel choisi selon vos besoins exacts. Le prix "
    "n'est donné qu'après un appel de cadrage avec un conseiller.\n"
    "Si vous voulez, dites-moi déjà : vos demandes arrivent surtout par WhatsApp, Instagram ou téléphone ? Et où perdez-vous le "
    "plus de monde : quand la réponse tarde, ou après avoir entendu un prix ?\n"
    "La prochaine étape est un court appel avec un conseiller. Validez-le sur la carte ici et nous l'organisons.",
    "أتمتة المحادثات تردّ فورًا على كل استفسار جديد، في أي ساعة. هكذا لا ينتظر العميل المحتمل ولا يذهب إلى منافس، ويبدو عملكم "
    "محترفًا ومتاحًا. وهي أيضًا تهيّئ الفئة المهتمة وتفرزها: من الجادّ، وما الذي يحتاجه، وما ميزانيته، قبل أن يتحدث معه أحد منكم. "
    "فيضيع وقت أقل بكثير على استفسارات غير مناسبة.\n"
    "كيف يعمل ذلك: منصتنا هي الجسر في الأساس. أحد أعضاء فريقنا يجلس معكم شخصيًا على كل الأتمتات وعلى مسارات المحادثة التي "
    "سيمرّ بها عملاؤكم قبل أن تتواصلوا معهم.\n"
    "التكلفة: رسوم إعداد لمرة واحدة، واشتراك مستمر في برنامج يُختار حسب احتياجكم الدقيق. يُعطى السعر فقط بعد مكالمة تحديد "
    "احتياجات مع ممثل.\n"
    "وإن أردتم، أخبروني الآن: هل تصلكم الاستفسارات غالبًا عبر واتساب أم إنستغرام أم الهاتف؟ وأين تخسرون أكثر الناس: حين يتأخر "
    "الرد، أم بعد أن يسمعوا السعر؟\n"
    "الخطوة التالية مكالمة قصيرة مع ممثل. وافقوا عليها في البطاقة هنا وننسّق الموعد.",
    "Автоматизация переписки сразу отвечает на каждое новое обращение, в любое время. Клиент не ждёт и не уходит к конкуренту, "
    "а ваш бизнес выглядит профессиональным и отзывчивым. Она ещё и «прогревает» и отбирает обращения: кто настроен серьёзно, "
    "что ему нужно, какой бюджет, ещё до того, как с ним заговорит кто-то из вас. Гораздо меньше времени уходит на нецелевые "
    "заявки.\n"
    "Как это устроено: наша платформа — в основном мост. Сотрудник нашей команды лично работает с вами над каждой автоматизацией "
    "и над сценариями диалога, которые пройдут ваши клиенты до того, как вы с ними свяжетесь.\n"
    "Стоимость: разовая плата за настройку плюс постоянная подписка на программу, подобранную под ваши точные задачи. Цену "
    "называем только после разговора с представителем и полного определения задач.\n"
    "Если хотите, расскажите уже сейчас: обращения приходят в основном в WhatsApp, в Instagram или по телефону? И где вы теряете "
    "больше всего людей: когда ответ задерживается или после того, как они слышат цену?\n"
    "Следующий шаг — короткий звонок с представителем. Подтвердите его в карточке здесь, и мы договоримся о времени.")

# The card (the ONE decision)
TITLE = _five("לקבוע שיחה עם נציג", "Schedule a call with a representative", "Planifier un appel avec un conseiller",
              "تحديد مكالمة مع ممثل", "Назначить звонок с представителем")
WHAT = _five(
    "נציג מהצוות שלנו יתקשר אליכם לשיחת אפיון: איך פניות מגיעות אליכם היום, מה שואלים הכי הרבה, ואיפה לקוחות נופלים. "
    "אחרי השיחה תקבלו הצעת מחיר: דמי הקמה חד-פעמיים, ומנוי שוטף לתוכנה שנבחר לפי הצורך המדויק שלכם. "
    "שום דבר לא נבנה ושום דבר לא מחויב לפני שאישרתם את ההצעה.",
    "A representative from our team will call you to map it out: how enquiries reach you today, what people ask most, and "
    "where customers drop off. After the call you'll get a quote: a one-off setup fee, plus an ongoing subscription to software "
    "chosen for your exact needs. Nothing is built and nothing is charged before you approve the quote.",
    "Un conseiller de notre équipe vous appellera pour cadrer le besoin : comment les demandes vous arrivent, ce qu'on vous "
    "demande le plus, où les clients décrochent. Après l'appel, vous recevrez un devis : des frais de mise en place uniques, plus "
    "un abonnement à un logiciel choisi selon vos besoins exacts. Rien n'est construit ni facturé avant votre accord sur le devis.",
    "سيتصل بكم ممثل من فريقنا لتحديد الاحتياجات: كيف تصلكم الاستفسارات اليوم، وما أكثر ما يُسأل، وأين يتوقف العملاء. بعد "
    "المكالمة تحصلون على عرض سعر: رسوم إعداد لمرة واحدة، واشتراك مستمر في برنامج يُختار حسب احتياجكم الدقيق. لا يُبنى شيء ولا "
    "يُحتسب أي مبلغ قبل موافقتكم على العرض.",
    "Представитель нашей команды позвонит вам, чтобы разобраться: как к вам сейчас приходят обращения, о чём спрашивают чаще "
    "всего и где теряются клиенты. После звонка вы получите предложение: разовая плата за настройку плюс постоянная подписка на "
    "программу, подобранную под ваши точные задачи. Ничего не создаётся и не оплачивается, пока вы не одобрите предложение.")
BUTTON = _five("כן, לקבוע שיחה", "Yes, schedule the call", "Oui, planifier l'appel", "نعم، حدّدوا المكالمة",
               "Да, назначить звонок")
DONE = _five(
    "קבעתי ✓ נציג מהצוות שלנו יחזור אליכם בדרך כלל תוך יום עסקים אחד כדי לתאם את השיחה.",
    "Done ✓ A representative from our team usually gets back to you within one business day to arrange the call.",
    "C'est noté ✓ Un conseiller de notre équipe vous recontacte en général sous un jour ouvré pour fixer l'appel.",
    "تم ✓ سيعود إليكم ممثل من فريقنا عادةً خلال يوم عمل واحد لتنسيق المكالمة.",
    "Готово ✓ Представитель нашей команды обычно связывается в течение одного рабочего дня, чтобы договориться о звонке.")
ALREADY = _five(
    "השיחה כבר מתואמת אצלנו ✓ נציג יחזור אליכם בדרך כלל תוך יום עסקים אחד.",
    "The call is already with us ✓ A representative usually gets back to you within one business day.",
    "L'appel est déjà demandé ✓ Un conseiller vous recontacte en général sous un jour ouvré.",
    "المكالمة مسجّلة لدينا بالفعل ✓ سيعود إليكم ممثل عادةً خلال يوم عمل واحد.",
    "Звонок уже запрошен ✓ Представитель обычно связывается в течение одного рабочего дня.")
FAILED = _five(
    "לא הצלחתי לרשום את הבקשה הפעם, ואני כבר מטפל בזה. נסו לאשר שוב בעוד רגע.",
    "I couldn't record the request this time, and I'm already on it. Try approving again in a moment.",
    "Je n'ai pas pu enregistrer la demande cette fois, je m'en occupe. Réessayez de valider dans un instant.",
    "لم أتمكن من تسجيل الطلب هذه المرة وأنا أتابع ذلك. حاولوا الموافقة مرة أخرى بعد لحظة.",
    "Не удалось записать запрос в этот раз, я уже разбираюсь. Попробуйте подтвердить ещё раз через минуту.")
TEAM_LABEL = _five("שיחת אפיון לאוטומציית שיחות", "A call to map out chat automation",
                   "Un appel de cadrage pour l'automatisation des conversations", "مكالمة لتحديد احتياجات أتمتة المحادثات",
                   "Звонок по автоматизации переписки")
DEFAULT_REQUEST = "שיחת אפיון לאוטומציית שיחות (הלקוח אישר בכרטיס)"

# Every client-facing table of this flow (tests/test_vendor_names.py reads it)
TEXT = {"pitch": PITCH, "title": TITLE, "what": WHAT, "button": BUTTON, "done": DONE, "already": ALREADY,
        "failed": FAILED, "team_label": TEAM_LABEL}


def pitch(lang: str = "he") -> str:
    return pick(PITCH, lang if lang in LANGS else "he")


# The manager's rule (support_agent._AUTOMATION_RULE). No braces: personas
# escape it for .format, and no vendor name: the model is never told one.
SALES_RULE = """

CHAT AUTOMATION (instant replies to the business's enquiries on its messaging channels) — A SALES
CONVERSATION THAT ENDS IN ONE CARD, NEVER A CONNECT FLOW:
- NEVER name the tool, software, platform vendor or brand behind it, in any language, even if the
  client names one or asks which one: say "the software we choose for your needs". The client's own
  channels (WhatsApp, Instagram, Messenger, the website) may be named as where their customers write.
- NEVER ask whether they already have an account anywhere, never give a sign-up link, never ask for a
  username, password or key, never describe setup steps, never propose a connect_* card for this.
- Sell it in your own voice, in plain business language, with ONE concrete example from THEIR kind of
  business when client_knowledge shows the industry (a clinic: "a new patient asks at 23:00 and gets an
  answer and a slot"; a shop: "someone asks about size and stock and gets it at once"). The value, in
  short: (1) an instant reply at any hour, so a lead never waits and doesn't go to a competitor; (2) a
  professional, responsive impression; (3) leads warmed up and qualified (who is serious, what they
  need, budget) before any human contact; (4) less time wasted on irrelevant enquiries.
- Ask about their situation as 2-3 READY GUESSES they can simply confirm or correct (how leads reach them
  today, what they answer most, where they lose people), e.g. "I'd guess most enquiries come on WhatsApp
  in the evening, and the ones you lose are the ones who wait for a reply. Close?". Never an empty
  questionnaire; one short block of guesses per reply at most.
- How it works, said plainly: our platform is mainly the bridge; a person from our team sits with the
  client personally on all the automations and on the conversation flows their customers will go
  through before the business contacts them.
- The cost, said plainly: a one-off setup fee plus an ongoing subscription to software chosen for their
  exact needs. NEVER a number, a range, a "from" price or an estimate: the price is given only after
  the call with a representative and a full characterisation.
- The ONLY next step is the card "book_automation_call" (schedule a call with a representative).
  Propose it in your first reply about this service and whenever they want to go ahead; put one short
  line of what they told you about their situation in its "about" param. Nothing is built before that
  call, and you never promise a date.
- NEVER set "upgrade_request" for it (the proposal builder would invent a price), and never open a
  handoff or team request for it: the card is the request.
- WhatsApp Business as a connected channel is not available yet: never promise it as part of this."""


def build(aa) -> dict:
    """The ONE card, registered into agent_actions.ACTIONS."""
    refusals = {"error": FAILED}

    def v_about(value, client_id):
        from core.redaction import redact
        return " ".join(redact(str(value or "")).split())[:300]

    def prepare(client_id, params, context, rows):
        if rows:
            return {"existing": rows[0]}     # one open card, never a second
        return {"params": params}

    def run(client_id, params, background_tasks):
        about = params.get("about") or ""
        request = f"{DEFAULT_REQUEST}: {about}" if about else DEFAULT_REQUEST
        out = aa.open_team_request(client_id, "general", TEAM_TOPIC, request, origin="client_chat")
        if out.get("error"):
            raise aa.ActionFailed("error", out["error"])
        return {"already": bool(out.get("already"))}

    return {
        CALL_ACTION: {
            "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
            "does": "The ONE next step for chat automation (instant replies to the business's enquiries): the "
                    "client asks for a call with a representative, who characterises their needs and only then "
                    "quotes (one-off setup fee + an ongoing software subscription). Approving opens a tracked team "
                    "request; nothing is built, connected or priced by the click.",
            "params": {"about": {"required": False, "validate": v_about,
                                 "hint": "optional: one short line of what the client said about their situation"}},
            "available": lambda state, client: True,
            "prepare": prepare,
            "validate_stored": lambda client_id, params: {"about": v_about((params or {}).get("about"), client_id)},
            "run": run,
            "title": TITLE,
            "what": WHAT,
            "button": BUTTON,
            "done": DONE,
            "done_message": lambda result, lang: pick(ALREADY if result.get("already") else DONE, lang),
            "refusals": refusals,
        },
    }
