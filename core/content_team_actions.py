"""The weekly content plan's cards (core/content_team) — registered into
agent_actions.ACTIONS.

- `approve_content_plan`  opened by code: the WHOLE week in one card — every
                          item with its day, channel and how it gets
                          published. One click approves all of it and starts
                          production.
- `build_content_plan`    what either agent proposes when the client asks for
                          a plan (or a new one): the plan is written and the
                          client gets the approval card straight away — one
                          decision, not two.
"""
from core import content_team

LANGS = ("he", "en", "fr", "ar", "ru")


def build(aa) -> dict:
    _txt, ActionFailed = aa._txt, aa.ActionFailed

    def _pick(texts, lang):
        return aa._pick(texts, lang)

    refusals = {
        "plan_missing": _txt(
            "התוכנית הזאת כבר לא פתוחה, אז לא אישרתי כלום. כתבו לי ואכין תוכנית חדשה.",
            "That plan is no longer open, so nothing was approved. Write to me and I'll prepare a new one.",
            "Ce plan n'est plus ouvert : rien n'a été validé. Écrivez-moi et j'en prépare un nouveau.",
            "هذه الخطة لم تعد مفتوحة، لذلك لم يُعتمد شيء. اكتبوا لي وسأجهّز خطة جديدة.",
            "Этот план уже не актуален, ничего не одобрено. Напишите — подготовлю новый."),
        "already_decided": _txt(
            "התוכנית הזאת כבר אושרה, וההפקה שלה בעבודה.", "That plan was already approved and is in production.",
            "Ce plan a déjà été validé et est en production.", "هذه الخطة معتمدة بالفعل وهي قيد الإنتاج.",
            "Этот план уже одобрен и находится в работе."),
        "planning_failed": _txt(
            "לא הצלחתי להכין את התוכנית הפעם, והצוות קיבל התראה. כתבו לי \"נסה שוב\" ואכין אותה מחדש.",
            "I couldn't prepare the plan this time, and the team was alerted. Write \"try again\" and I'll prepare it again.",
            "Je n'ai pas pu préparer le plan cette fois ; l'équipe est prévenue. Écrivez « réessaie » et je le prépare à nouveau.",
            "لم أتمكن من تجهيز الخطة هذه المرة وتم تنبيه الفريق. اكتبوا \"حاول مجددًا\" وسأجهّزها من جديد.",
            "В этот раз план не получился, команда уведомлена. Напишите «ещё раз» — подготовлю снова."),
    }
    waiting = _txt("כבר יש תוכנית שמחכה לאישור שלכם. הנה היא:", "There is already a plan waiting for your approval. Here it is:",
                   "Un plan attend déjà votre validation. Le voici :", "هناك خطة بانتظار موافقتكم بالفعل. ها هي:",
                   "План уже ждёт вашего одобрения. Вот он:")
    prepared = _txt("הכנתי את התוכנית לשבוע הקרוב. הכול בכרטיס אחד:", "I prepared the plan for the coming week. It is all in one card:",
                    "J'ai préparé le plan de la semaine à venir. Tout est dans une seule carte :",
                    "جهّزت خطة الأسبوع القادم. كل شيء في بطاقة واحدة:", "Я подготовил план на ближайшую неделю. Всё в одной карточке:")

    # ── approve_content_plan ────────────────────────────────────────────────
    def v_plan(value, client_id):
        plan_id = str(value or "")
        if not plan_id or content_team.open_plan(client_id).get("id") != plan_id:
            raise ValueError("no such open plan")
        return plan_id

    def prepare_approve(client_id, params, context, rows):
        clean = {"plan": params["plan"]}
        for row in rows:
            if (row.get("params") or {}) == clean:
                return {"existing": row}
        return {"params": clean, "supersedes": rows[0].get("id") if rows else None}

    def display_plan(client_id, params):
        lang = aa._lang(client_id)
        value = content_team.latest(client_id)
        if not value or value.get("id") != str(params.get("plan")):
            return {"lines": "", "why": "", "team": ""}
        modes = content_team.channel_modes(client_id)
        manual = any(modes.get(i["platform"]) != "auto" for i in value["items"])
        return {"lines": content_team.lines(value, lang, modes),
                "why": content_team.pick(content_team.TEXT["why_manual"], lang) if manual else "",
                "team": content_team.pick(content_team.TEXT["team"], lang)}

    def run_approve(client_id, params, background_tasks):
        try:
            value = content_team.approve(client_id, params["plan"])
        except content_team.PlanError as e:
            raise ActionFailed(e.code if e.code in refusals else "error", str(e))
        # ליאור's production takes minutes: never inside the client's click
        if background_tasks is not None:
            background_tasks.add_task(content_team.produce, client_id, value["id"])
        else:
            content_team.produce(client_id, value["id"])
        return {"count": len(value["items"])}

    # ── build_content_plan ──────────────────────────────────────────────────
    def prepare_build(client_id, params, context, rows):
        lang = (context or {}).get("language") or aa._lang(client_id)
        existing = content_team.open_plan(client_id)
        if existing and not params.get("replace"):
            return {"switch_to": "approve_content_plan", "switch_params": {"plan": existing["id"]},
                    "notice": _pick(waiting, lang)}
        try:
            from agents import media_agent
            from core import israel_calendar
            client = dict(aa._client(client_id) or {}, id=client_id)
            items = media_agent.plan_week(client, israel_calendar.upcoming_events(), request=params.get("request", ""))
            value = content_team.store(client_id, items, lang, source="chat")
        except Exception as e:
            aa._alert(f"client {client_id}: the weekly content plan could not be prepared from the chat "
                      f"({type(e).__name__})")
            return {"refuse": _pick(refusals["planning_failed"], lang)}
        if not value:
            return {"refuse": _pick(refusals["planning_failed"], lang)}
        return {"switch_to": "approve_content_plan", "switch_params": {"plan": value["id"]},
                "notice": _pick(prepared, lang)}

    def v_request(value, client_id):
        return aa._v_short_text(value, 300)

    def v_replace(value, client_id):
        return bool(value) and str(value).lower() not in ("false", "0", "no")

    approved = _txt(
        "אושר ✓ ליאור מתחיל להפיק את {count} הפריטים, ומאיה מחזיקה את הלוח. כל פריט יופיע בלשונית ״מדיה״ כשהוא מוכן, עם היום שלו והכיתוב שלו.",
        "Approved ✓ Lior starts producing the {count} items, and Maya keeps the schedule. Each item appears in the Media tab when it is ready, with its day and its caption.",
        "Validé ✓ Lior commence à produire les {count} contenus et Maya tient le calendrier. Chaque contenu apparaît dans l'onglet Médias dès qu'il est prêt, avec son jour et sa légende.",
        "تمت الموافقة ✓ ليئور يبدأ إنتاج {count} عناصر ومايا تمسك بالجدول. يظهر كل عنصر في تبويب الوسائط عند جاهزيته مع يومه ونصّه.",
        "Одобрено ✓ Лиор начинает готовить материалы ({count}), а Майя ведёт расписание. Каждый материал появится во вкладке «Медиа», когда будет готов, со своим днём и подписью.")

    return {
        "approve_content_plan": {
            "approver": "client", "client_requestable": False, "internal_only": False, "kind": "execute",
            "code_only": True,
            "does": "Shown by the system: the client's WHOLE weekly content plan in one card. One click approves "
                    "every item and starts production.",
            "params": {"plan": {"required": True, "validate": v_plan, "hint": ""}},
            "available": lambda state, client: False,
            "prepare": prepare_approve,
            "display": display_plan,
            "run": run_approve,
            "button": _txt("מאשר/ת את כל התוכנית", "Approve the whole plan", "Valider tout le plan", "الموافقة على الخطة كلها",
                           "Одобрить весь план"),
            "title": _txt("תוכנית התוכן לשבוע הקרוב", "Next week's content plan", "Le plan de contenu de la semaine",
                          "خطة محتوى الأسبوع القادم", "План контента на неделю"),
            "what": _txt(
                "זו התוכנית לשבוע הקרוב:\n{lines}\n{team} {why}\nאישור אחד מאשר את כל התוכנית ומתחיל את ההפקה. רוצים לשנות פריט? אל תאשרו, כתבו לי מה לשנות ואכין תוכנית מעודכנת.",
                "This is the plan for the coming week:\n{lines}\n{team} {why}\nOne approval approves the whole plan and starts production. Want an item changed? Don't approve; tell me what to change and I'll prepare an updated plan.",
                "Voici le plan de la semaine à venir :\n{lines}\n{team} {why}\nUne seule validation approuve tout le plan et lance la production. Un contenu à modifier ? Ne validez pas ; dites-moi quoi changer et je prépare un plan mis à jour.",
                "هذه خطة الأسبوع القادم:\n{lines}\n{team} {why}\nموافقة واحدة تعتمد الخطة كلها وتبدأ الإنتاج. تريدون تغيير عنصر؟ لا توافقوا؛ اكتبوا لي ما المطلوب تغييره وسأجهّز خطة محدّثة.",
                "Это план на ближайшую неделю:\n{lines}\n{team} {why}\nОдно одобрение утверждает весь план и запускает производство. Хотите изменить пункт? Не одобряйте; напишите, что изменить, и я подготовлю обновлённый план."),
            "done": approved,
            "done_message": lambda result, lang: _pick(approved, lang).replace("{count}", str(result.get("count", ""))),
            "cancelled": _txt(
                "בסדר, לא אישרתי כלום ושום דבר לא יופק. כתבו לי מה לשנות ואכין תוכנית אחרת.",
                "All right, nothing was approved and nothing will be produced. Tell me what to change and I'll prepare another plan.",
                "D'accord, rien n'a été validé et rien ne sera produit. Dites-moi quoi changer et je prépare un autre plan.",
                "حسنًا، لم يُعتمد شيء ولن يُنتج شيء. اكتبوا لي ما المطلوب تغييره وسأجهّز خطة أخرى.",
                "Хорошо, ничего не одобрено и ничего не будет изготовлено. Напишите, что изменить, и я подготовлю другой план."),
            "refusals": {k: refusals[k] for k in ("plan_missing", "already_decided")},
        },
        "build_content_plan": {
            "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
            "needs_row": True,
            "does": "CONTENT TEAM: prepares the client's weekly content plan (2-3 visual items, each with its day, "
                    "channel and caption) and shows it as ONE approval card — the system writes the plan and opens "
                    "that card itself. Propose it when the client asks for a content plan, for posts for the week, or "
                    "asks to change the plan that is waiting (then replace=true and their change in request). "
                    "Whoever you are — the planner and the producer are one team.",
            "params": {"request": {"required": False, "validate": v_request,
                                   "hint": "optional, the client's language: what they asked the plan to focus on or "
                                           "to change"},
                       "replace": {"required": False, "validate": v_replace,
                                   "hint": "true only when they asked to change a plan that is waiting for approval"}},
            "available": lambda state, client: True,
            "prepare": prepare_build,
            "run": lambda client_id, params, background_tasks, row=None: {},     # never reached: prepare always switches
            "title": _txt("תוכנית תוכן שבועית", "Weekly content plan", "Plan de contenu hebdomadaire", "خطة محتوى أسبوعية",
                          "Недельный план контента"),
            "what": _txt("אכין לכם תוכנית תוכן לשבוע הקרוב, לאישור בכרטיס אחד.",
                         "I'll prepare a content plan for the coming week, for your approval in one card.",
                         "Je prépare un plan de contenu pour la semaine à venir, à valider en une seule carte.",
                         "سأجهّز خطة محتوى للأسبوع القادم لتوافقوا عليها في بطاقة واحدة.",
                         "Я подготовлю план контента на ближайшую неделю — на одобрение в одной карточке."),
            "done": _txt("התוכנית מוכנה.", "The plan is ready.", "Le plan est prêt.", "الخطة جاهزة.", "План готов."),
            "refusals": {"planning_failed": refusals["planning_failed"]},
        },
    }
