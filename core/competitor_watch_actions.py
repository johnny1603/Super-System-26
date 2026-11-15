"""מאיה's competitor cards (core/competitor_watch) — registered into agent_actions.ACTIONS.

- `track_competitor`   the client adds a competitor (name + optionally one public
                       link) to the watch list. Nothing is read by the click.
- `competitor_digest`  the weekly read: what works for them, what doesn't, how
                       we do better and more creatively. Free; one model call;
                       public numbers from YouTube only; never copies anything.
"""
from core import competitor_watch as cw

LANGS = ("he", "en", "fr", "ar", "ru")


def build(aa) -> dict:
    _txt, ActionFailed = aa._txt, aa.ActionFailed

    refusals = {
        "no_competitors": _txt(
            "עוד אין לי מתחרים לעקוב אחריהם. כתבו לי שם של עסק אחד שאתם מתחרים בו (ואם יש, קישור לעמוד הציבורי שלו), ואוסיף אותו.",
            "I don't have any competitors to follow yet. Tell me one business you compete with (and, if you have it, a link to its public page), and I'll add it.",
            "Je n'ai encore aucun concurrent à suivre. Donnez-moi une entreprise concurrente (et si possible le lien de sa page publique), je l'ajoute.",
            "لا يوجد لديّ منافسون أتابعهم بعد. اكتبوا لي اسم عمل واحد تنافسونه (ورابط صفحته العامة إن وجد) وسأضيفه.",
            "Пока мне не за кем следить. Напишите один бизнес-конкурент (и, если есть, ссылку на его публичную страницу) — я добавлю."),
        "nothing_found": _txt(
            "השבוע לא מצאתי אצל המתחרים משהו שאני יכול לעמוד מאחוריו, אז לא כתבתי סתם. אם יש להם ערוץ יוטיוב, שלחו לי קישור, ושם אני רואה מספרים אמיתיים.",
            "This week I found nothing at the competitors I can stand behind, so I didn't write filler. If they have a YouTube channel, send me the link; there I see real numbers.",
            "Cette semaine je n'ai rien trouvé de solide chez les concurrents, donc je n'ai rien inventé. S'ils ont une chaîne YouTube, envoyez-moi le lien : là je vois de vrais chiffres.",
            "لم أجد هذا الأسبوع لدى المنافسين شيئًا أستطيع الاعتماد عليه، فلم أكتب حشوًا. إن كانت لديهم قناة يوتيوب أرسلوا الرابط؛ هناك أرى أرقامًا حقيقية.",
            "На этой неделе я не нашла у конкурентов ничего надёжного, поэтому не писала воду. Если у них есть YouTube-канал, пришлите ссылку — там я вижу реальные цифры."),
        "error": _txt(
            "הסקירה לא הסתיימה הפעם, ואני כבר מטפלת בזה. כתבו לי \"נסי שוב\" ואריץ אותה מחדש.",
            "The read didn't finish this time, and I'm already on it. Write \"try again\" and I'll rerun it.",
            "L'analyse n'a pas abouti cette fois ; je m'en occupe. Écrivez « réessaie » et je la relance.",
            "لم تكتمل المراجعة هذه المرة وأنا أتابعها. اكتبوا \"حاولي مجددًا\" وسأعيدها.",
            "Обзор в этот раз не завершился, я уже разбираюсь. Напишите «ещё раз» — запущу снова."),
    }

    def v_name(value, client_id):
        name = " ".join(str(value or "").split())[:80]
        if len(name) < 2:
            raise ValueError("competitor name too short")
        return name

    def v_url(value, client_id):
        url = cw._clean_url(value)
        if not url:
            raise ValueError("not a public link")
        return url

    def run_track(client_id, params, background_tasks):
        listed = cw.add(client_id, params["name"], params.get("url", ""))
        link = params.get("url", "")
        return {"count": len(listed), "access": cw.PLATFORM_ACCESS.get(cw.classify(link)["platform"], "observed") if link else ""}

    access_line = {
        "measured": _txt("ביוטיוב אני רואה את המספרים הציבוריים שלו.", "On YouTube I can see its public numbers.",
                         "Sur YouTube je vois ses chiffres publics.", "في يوتيوب أرى أرقامه العامة.", "На YouTube я вижу его публичные цифры."),
        "needs_permission": _txt("באינסטגרם אני עוד לא יכולה לקרוא מספרים של חשבונות אחרים, אז שם זו התרשמות מחיפוש ציבורי.",
                                 "On Instagram I can't read other accounts' numbers yet, so there it's an impression from public search.",
                                 "Sur Instagram je ne peux pas encore lire les chiffres d'autres comptes : impression issue de la recherche publique.",
                                 "في إنستغرام لا أستطيع بعد قراءة أرقام حسابات أخرى، فهناك انطباع من البحث العام.",
                                 "В Instagram я пока не могу читать цифры чужих аккаунтов — там впечатление из публичного поиска."),
        "not_available": _txt("בפלטפורמה הזו אין דרך מותרת לקרוא תוכן של חשבונות אחרים, אז שם זו התרשמות מחיפוש ציבורי.",
                              "That platform has no permitted way to read other accounts' content, so there it's an impression from public search.",
                              "Cette plateforme n'offre aucune méthode autorisée pour lire d'autres comptes : impression issue de la recherche publique.",
                              "هذه المنصة لا تتيح طريقة مسموحة لقراءة حسابات أخرى، فهناك انطباع من البحث العام.",
                              "Эта платформа не даёт разрешённого способа читать чужие аккаунты — там впечатление из публичного поиска."),
        "observed": _txt("", "", "", "", ""),
    }
    tracked = _txt("הוספתי ✓ עכשיו אני עוקבת אחרי {count} מתחרים. {access}",
                   "Added ✓ I'm now following {count} competitors. {access}",
                   "Ajouté ✓ Je suis maintenant {count} concurrents. {access}",
                   "تمت الإضافة ✓ أتابع الآن {count} منافسين. {access}",
                   "Добавлено ✓ Теперь я слежу за конкурентами: {count}. {access}")

    def run_digest(client_id, params, background_tasks, row=None):
        lang = (row or {}).get("language") or "he"
        try:
            digest = cw.build_digest(client_id, lang)
        except cw.WatchError as e:
            raise ActionFailed(e.code, e.code)
        except Exception as e:
            aa._alert(f"client {client_id}: the competitor read failed ({type(e).__name__}: {str(e)[:200]})")
            raise ActionFailed("error", type(e).__name__)
        return {"text": cw.message(digest, lang)}

    def prepare_digest(client_id, params, context, rows):
        lang = (context or {}).get("language") or "he"
        if not cw.watch_list(client_id):
            return {"clarify": aa._pick(refusals["no_competitors"], lang)}
        if rows:
            return {"existing": rows[0]}
        return {"params": {}}

    return {
        "track_competitor": {
            "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
            "does": "Adds ONE competitor the client named to מאיה's watch list (competitor_watch.competitors), with "
                    "optionally ONE public link to their page or channel exactly as the client gave it. Never a "
                    "competitor the client did not name. Nothing is read by the click.",
            "params": {"name": {"required": True, "validate": v_name, "hint": "the competitor's name, as the client wrote it"},
                       "url": {"required": False, "validate": v_url,
                               "hint": "optional: ONE public link the client gave (YouTube channel, Instagram, site)"}},
            "available": lambda state, client: True,
            "run": run_track,
            "title": _txt("לעקוב אחרי מתחרה", "Follow a competitor", "Suivre un concurrent", "متابعة منافس",
                          "Следить за конкурентом"),
            "what": _txt("אוסיף את {name} לרשימת המתחרים שאני עוקבת אחריהם, ואסתכל רק על מה שפומבי. אנחנו לא מעתיקים תוכן של אף אחד.",
                         "I'll add {name} to the competitors I follow, and look only at what is public. We never copy anyone's content.",
                         "J'ajoute {name} aux concurrents que je suis, en ne regardant que ce qui est public. Nous ne copions le contenu de personne.",
                         "سأضيف {name} إلى المنافسين الذين أتابعهم، وأنظر فقط إلى ما هو عام. لا ننسخ محتوى أحد.",
                         "Я добавлю {name} в список конкурентов и буду смотреть только публичное. Мы ничего не копируем."),
            "display": lambda client_id, params: {"name": params.get("name", "")},
            "done": tracked,
            "done_message": lambda result, lang: aa._pick(tracked, lang).replace("{count}", str(result.get("count", 0)))
            .replace("{access}", aa._pick(access_line.get(result.get("access") or "observed", access_line["observed"]), lang)).strip(),
        },
        "competitor_digest": {
            "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
            "needs_row": True,
            "does": "מאיה's weekly read of the competitors' PUBLIC social presence (competitor_watch): what works for them, "
                    "what doesn't, and ORIGINAL ideas for this client to do better and more creatively. Real numbers only "
                    "from public YouTube channels; elsewhere an impression from public search, said so. Never copies a "
                    "caption, creative or trademark. Free; at most one fresh read a week.",
            "params": {},
            "available": lambda state, client: True,
            "prepare": prepare_digest,
            "validate_stored": lambda client_id, params: {},
            "run": run_digest,
            "slow": {"minutes": 5, "eta": _txt("הסקירה לוקחת בדרך כלל דקה-שתיים.", "The read usually takes a minute or two.",
                                               "L'analyse prend en général une à deux minutes.", "تستغرق المراجعة عادةً دقيقة أو دقيقتين.",
                                               "Обзор обычно занимает минуту-две.")},
            "title": _txt("מה קורה אצל המתחרים", "What the competitors are doing", "Ce que font les concurrents",
                          "ماذا يفعل المنافسون", "Что делают конкуренты"),
            "what": _txt("אסתכל על מה שהמתחרים שלכם מפרסמים בפומבי: מה עובד להם, מה פחות, ואיך אנחנו עושים את זה טוב יותר ויצירתי יותר, בדרך שלכם. ביוטיוב אני רואה מספרים ציבוריים אמיתיים; בשאר הרשתות זו התרשמות מחיפוש ציבורי. אנחנו לא מעתיקים כלום, זו השראה בלבד.",
                         "I'll look at what your competitors publish publicly: what works for them, what doesn't, and how we do it better and more creatively, your way. On YouTube I see real public numbers; elsewhere it's an impression from public search. We copy nothing; it's inspiration only.",
                         "Je regarde ce que vos concurrents publient publiquement : ce qui marche, ce qui marche moins, et comment faire mieux et plus créatif, à votre façon. Sur YouTube je vois de vrais chiffres publics ; ailleurs c'est une impression issue de la recherche publique. Nous ne copions rien ; c'est de l'inspiration.",
                         "سأنظر إلى ما ينشره منافسوكم علنًا: ما ينجح لديهم وما ينجح أقل، وكيف نفعلها أفضل وأكثر إبداعًا وبأسلوبكم. في يوتيوب أرى أرقامًا عامة حقيقية؛ وفي غيره انطباع من البحث العام. لا ننسخ شيئًا؛ هذا إلهام فقط.",
                         "Я посмотрю, что ваши конкуренты публикуют открыто: что у них работает, что хуже и как сделать лучше и креативнее — по-вашему. На YouTube я вижу реальные публичные цифры; в остальных сетях — впечатление из публичного поиска. Мы ничего не копируем, только вдохновение."),
            "done": _txt("{text}", "{text}", "{text}", "{text}", "{text}"),
            "done_message": lambda result, lang: result.get("text", ""),
            "refusals": refusals,
        },
    }
