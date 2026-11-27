"""The Google page's two cards (core/google_page) — registered into
agent_actions.ACTIONS.

- `write_google_strategy`  Yoav writes (or rewrites) the strategy the client
                           reads on the Google page: ONE model call that also
                           explains every search phrase and decides every
                           note the client left on a phrase. Part of the
                           service: nothing is charged.
- `build_google_report`    a report from the account's figures of that day,
                           kept on the page (history) and downloadable. No
                           model call. Refused, with what is missing, when the
                           account gives no current figures.
"""
from core import google_page

ACTIONS = ("write_google_strategy", "build_google_report")


def build(aa) -> dict:
    _txt, ActionFailed = aa._txt, aa.ActionFailed

    def _pick(texts, lang):
        return aa._pick(texts, lang)

    def _where(lang):
        return google_page.WHERE.get(lang, google_page.WHERE["en"])

    strategy_refusals = {
        "nothing_to_plan": _txt(
            "עוד אין לנו קמפיין מוכן או תקציב חודשי רשום, אז אין על מה לכתוב אסטרטגיה. נתחיל מהכנת קמפיין.",
            "We have no prepared campaign and no monthly budget on record yet, so there is nothing to write a strategy about. Let's start by preparing a campaign.",
            "Nous n'avons encore ni campagne préparée ni budget mensuel enregistré ; il n'y a donc rien sur quoi écrire une stratégie. Commençons par préparer une campagne.",
            "لا توجد لدينا بعد حملة جاهزة ولا ميزانية شهرية مسجلة، فلا شيء نكتب عنه استراتيجية. لنبدأ بتجهيز حملة.",
            "У нас пока нет подготовленной кампании и не записан месячный бюджет, поэтому писать стратегию не о чем. Начнём с подготовки кампании."),
        "writing_failed": _txt(
            "כתיבת האסטרטגיה לא הצליחה הפעם, והצוות קיבל התראה. כתבו לי \"נסה שוב\" ואריץ מחדש.",
            "Writing the strategy didn't work this time, and the team was alerted. Write \"try again\" and I'll rerun it.",
            "La rédaction de la stratégie n'a pas abouti cette fois ; l'équipe est prévenue. Écrivez « réessaie » et je relance.",
            "لم تنجح كتابة الاستراتيجية هذه المرة وتم تنبيه الفريق. اكتبوا \"حاول مجددًا\" وسأعيد التنفيذ.",
            "В этот раз стратегия не написалась, команда уведомлена. Напишите «ещё раз» — запущу снова."),
    }
    strategy_done = _txt(
        "האסטרטגיה כתובה. היא מחכה לכם כאן: {where}, בחלק ״האסטרטגיה״. כתבתי שם גם מה יגרום לנו לשנות כיוון, ומתי נדע.",
        "The strategy is written. It's waiting for you here: {where}, under \"The strategy\". I also wrote there what would make us change course, and by when we will know.",
        "La stratégie est rédigée. Elle vous attend ici : {where}, dans « La stratégie ». J'y ai aussi écrit ce qui nous ferait changer de cap, et quand nous le saurons.",
        "الاستراتيجية مكتوبة. تجدونها هنا: {where}، في قسم \"الاستراتيجية\". كتبت هناك أيضًا ما الذي سيجعلنا نغيّر الاتجاه ومتى سنعرف.",
        "Стратегия написана. Она здесь: {where}, в разделе «Стратегия». Там же я написал, что заставит нас сменить курс и когда мы это узнаем.")
    report_refusals = {
        "no_numbers": _txt(
            "אי אפשר להפיק דוח כרגע, כי אין מספרים עדכניים מחשבון הפרסום שלכם בגוגל. ב{where} כתוב מה חסר ומה צריך כדי שזה יעבוד.",
            "A report can't be produced right now, because there are no current numbers from your Google advertising account. {where} says what is missing and what it takes.",
            "Impossible de produire un rapport pour l'instant : pas de chiffres à jour de votre compte publicitaire Google. {where} indique ce qui manque et ce qu'il faut.",
            "لا يمكن إنتاج تقرير الآن لعدم وجود أرقام محدثة من حسابكم الإعلاني على جوجل. في {where} مكتوب ما الناقص وما المطلوب.",
            "Отчёт сейчас сделать нельзя: нет свежих цифр из вашего рекламного аккаунта Google. {where} — там написано, чего не хватает и что нужно."),
        "storage": _txt(
            "לא הצלחתי לשמור את הדוח כרגע. נסו שוב בעוד כמה דקות.", "I couldn't save the report right now. Please try again in a few minutes.",
            "Je n'ai pas pu enregistrer le rapport pour l'instant. Réessayez dans quelques minutes.", "تعذّر حفظ التقرير الآن. حاولوا مجددًا بعد دقائق.",
            "Сейчас не удалось сохранить отчёт. Попробуйте через несколько минут."),
    }
    report_done = _txt(
        "הדוח מוכן. הוא מחכה לכם כאן: {where}, בחלק ״דוחות״. אפשר לקרוא אותו בעמוד וגם להוריד.",
        "The report is ready. It's waiting for you here: {where}, under \"Reports\". You can read it on the page and download it.",
        "Le rapport est prêt. Il vous attend ici : {where}, dans « Rapports ». Vous pouvez le lire sur la page et le télécharger.",
        "التقرير جاهز. تجدونه هنا: {where}، في قسم \"التقارير\". يمكنكم قراءته في الصفحة وتنزيله.",
        "Отчёт готов. Он здесь: {where}, в разделе «Отчёты». Его можно прочитать на странице и скачать.")

    def _refusal(table, code, lang):
        return _pick(table[code], lang).replace("{where}", _where(lang))

    def v_reason(value, client_id):
        return aa._v_short_text(value, 400)

    def prepare_strategy(client_id, params, context, rows):
        lang = (context or {}).get("language") or "he"
        if not google_page.has_material(client_id):
            return {"refuse": _refusal(strategy_refusals, "nothing_to_plan", lang)}
        if rows:
            return {"existing": rows[0]}
        return {"params": {"reason": params["reason"]} if params.get("reason") else {}}

    def validate_strategy(client_id, params):
        params = params if isinstance(params, dict) else {}
        if set(params) - {"reason"}:
            raise ValueError("unknown params")
        return {"reason": v_reason(params["reason"], client_id)} if params.get("reason") else {}

    def run_strategy(client_id, params, background_tasks, row=None, call=None):
        from core import governance
        lang = (row or {}).get("language") or aa._lang(client_id)
        try:
            with governance.lane_scope("system"):          # the strategy is our own work, never the client's credits
                return google_page.write_strategy(client_id, lang, params.get("reason", ""), call=call)
        except google_page.PageError as e:
            if e.code != "nothing_to_plan":
                aa._alert(f"client {client_id}: the Google strategy could not be written ({str(e)[:240]})")
            raise ActionFailed(e.code if e.code in strategy_refusals else "writing_failed", str(e))
        except Exception as e:
            aa._alert(f"client {client_id}: the Google strategy could not be stored ({type(e).__name__})")
            raise ActionFailed("writing_failed", type(e).__name__)

    def prepare_report(client_id, params, context, rows):
        lang = (context or {}).get("language") or "he"
        if google_page.figures(client_id)["state"] != "live":
            return {"refuse": _refusal(report_refusals, "no_numbers", lang)}
        if rows:
            return {"existing": rows[0]}
        return {"params": {}}

    def validate_report(client_id, params):
        if params:
            raise ValueError("unknown params")
        return {}

    def run_report(client_id, params, background_tasks, row=None):
        lang = (row or {}).get("language") or aa._lang(client_id)
        try:
            built = google_page.build_report(client_id, lang)
        except google_page.PageError as e:
            raise ActionFailed("no_numbers", str(e))
        except Exception as e:
            raise ActionFailed("storage", type(e).__name__)
        return {"report": built["id"], "since": built["since"], "until": built["until"]}

    return {
        "write_google_strategy": {
            "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute", "needs_row": True,
            "does": "GOOGLE STRATEGY: Yoav writes (or rewrites) the strategy shown on the client's Google page — what we do "
                    "with the money and why, what it assumes, the two or three numbers we watch, and what would make us "
                    "change course and by when. The same run explains why each search phrase was chosen and decides the "
                    "notes the client left on single phrases. Propose it when they ask what the plan or strategy is, why "
                    "the budget is split this way, why a search phrase was chosen, or when the plan really changed. Free.",
            "params": {"reason": {"required": False, "validate": v_reason,
                                  "hint": "optional, the client's language: why it is being rewritten now (what changed)"}},
            "available": lambda state, client: True,
            "prepare": prepare_strategy,
            "validate_stored": validate_strategy,
            "run": run_strategy,
            "slow": {"minutes": 4, "eta": _txt(
                "כתיבת אסטרטגיה לוקחת בדרך כלל כדקה.", "Writing a strategy usually takes about a minute.",
                "Rédiger une stratégie prend en général une minute environ.", "كتابة الاستراتيجية تستغرق عادةً نحو دقيقة.",
                "Написание стратегии обычно занимает около минуты.")},
            "title": _txt("האסטרטגיה שלנו בגוגל", "Our Google strategy", "Notre stratégie Google", "استراتيجيتنا على جوجل", "Наша стратегия в Google"),
            "what": _txt(
                "אכתוב לכם את האסטרטגיה בגוגל: מה אנחנו עושים עם הכסף ולמה, על מה זה נשען, על אילו מספרים אנחנו מסתכלים, ומה יגרום לנו לשנות כיוון ומתי נדע. היא תופיע בעמוד ״גוגל״, והגרסאות הקודמות נשמרות שם.",
                "I'll write your Google strategy: what we do with the money and why, what it assumes, which numbers we watch, and what would make us change course and by when. It appears on the Google page, and earlier versions stay there.",
                "Je rédige votre stratégie Google : ce que nous faisons de l'argent et pourquoi, ce que cela suppose, les chiffres que nous surveillons, et ce qui nous ferait changer de cap et quand. Elle apparaît sur la page Google, où les versions précédentes restent visibles.",
                "سأكتب لكم الاستراتيجية على جوجل: ماذا نفعل بالمال ولماذا، على ماذا تستند، أي أرقام نراقب، وما الذي سيجعلنا نغيّر الاتجاه ومتى سنعرف. ستظهر في صفحة \"جوجل\" وتبقى النسخ السابقة هناك.",
                "Я напишу стратегию в Google: что мы делаем с деньгами и почему, на чём это основано, за какими цифрами следим, что заставит нас сменить курс и когда мы это узнаем. Она появится на странице «Google», прежние версии сохраняются там же."),
            "done": strategy_done,
            "done_message": lambda result, lang: _pick(strategy_done, lang).replace("{where}", _where(lang)),
            "refusals": strategy_refusals,
        },
        "build_google_report": {
            "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute", "needs_row": True,
            "does": "GOOGLE REPORT: builds a report of the client's Google advertising from the account's own figures (the "
                    "budget, and the results of each lane separately) and keeps it on their Google page, readable there and "
                    "downloadable, next to the earlier ones. Propose it when they ask for a report on their Google ads. "
                    "Free. If the account gives no current figures the system refuses and says what is missing.",
            "params": {},
            "available": lambda state, client: True,
            "prepare": prepare_report,
            "validate_stored": validate_report,
            "run": run_report,
            "title": _txt("דוח גוגל", "Google report", "Rapport Google", "تقرير جوجل", "Отчёт Google"),
            "what": _txt(
                "אפיק לכם דוח על הפרסום בגוגל מהמספרים של החשבון: התקציב, והתוצאות של כל מסלול בנפרד. הוא יופיע בעמוד ״גוגל״, לקריאה ולהורדה, לצד הדוחות הקודמים.",
                "I'll produce a report on your Google advertising from the account's numbers: the budget, and the results of each track separately. It appears on the Google page, to read and download, next to the earlier reports.",
                "Je produis un rapport sur votre publicité Google à partir des chiffres du compte : le budget, et les résultats de chaque volet séparément. Il apparaît sur la page Google, à lire et à télécharger, à côté des rapports précédents.",
                "سأنتج لكم تقريرًا عن الإعلان على جوجل من أرقام الحساب: الميزانية، ونتائج كل مسار منفردًا. سيظهر في صفحة \"جوجل\" للقراءة والتنزيل بجانب التقارير السابقة.",
                "Я сделаю отчёт о рекламе в Google по цифрам аккаунта: бюджет и результаты каждого направления отдельно. Он появится на странице «Google» — читать и скачать, рядом с прошлыми отчётами."),
            "done": report_done,
            "done_message": lambda result, lang: _pick(report_done, lang).replace("{where}", _where(lang)),
            "refusals": report_refusals,
        },
    }
