"""The 3-month goals card (core/quarter_goals) — registered into
agent_actions.ACTIONS.

- `agree_quarter_goals`  the manager proposes it after the deep
                         characterisation: ONE card with the three tracks
                         (posts a week run by מאיה, the organic keywords, what
                         must improve). The client's click stores them; a new
                         card replaces the old goals. Nothing runs, nothing is
                         charged, nothing is promised.

Organic in the professional order (2026-11-16, docs/ORGANIC-PLAN.md): for a
package with organic, the card is only offered once core/organic_plan is
READY; its keywords are the client-APPROVED set (never the model's words), the
plan's time estimate (internal + committed, +15%) is shown, and the terms of
measurement are on the card before the click. The click starts the clock and
captures the dated baseline (quarter_goals.capture_baseline).
"""
import re

from core import quarter_goals, target_inputs

# A yes/no goal can't be measured at 90% (terms §6.3): "page one", "top 3",
# "first place" are refused here and expressed as positions to gain instead.
YES_NO_GOAL = re.compile(
    r"(עמוד\s*(ה)?ראשון|דף\s*(ה)?ראשון|מקום\s*(ה)?ראשון|טופ\s*\d+|page\s*(one|1)\b|first\s*page|top\s*\d+|"
    r"number\s*one|#\s*1\b|first\s*place|premi[eè]re\s*page|premi[eè]re\s*place|الصفحة\s*الأولى|المركز\s*الأول|"
    r"перв(ая|ой|ую)\s*страниц|перв(ое|ом)\s*мест|топ\s*\d+)", re.I)

LANGS = ("he", "en", "fr", "ar", "ru")

# The terms of MEASUREMENT, shown on the card before the click. They describe what the
# system does; the money side of the commitment (the management fee if goals are missed)
# is the service agreement's to state — see docs/ORGANIC-PLAN.md §The commitment. Not legal text.
TERMS = {
    "he": "איך נמדוד: רק על הביטויים שסיכמנו ({k}) ועל המדד שסיכמנו, מול נקודת התחלה שנשמרת היום עם התאריך. דוח פעם בחודש מול נקודת ההתחלה הזו. כל הערכה מסומנת כהערכה. ההתחייבות שלנו נמדדת רק על אלה, לפי תנאי השירות.",
    "en": "How we measure: only on the phrases we agreed ({k}) and the metric we agreed, against a starting point saved today with its date. A report every month against that starting point. Every estimate is labelled as one. Our commitment is measured on these only, under the service terms.",
    "fr": "Comment nous mesurons : uniquement sur les expressions convenues ({k}) et l'indicateur convenu, par rapport à un point de départ enregistré aujourd'hui avec sa date. Un rapport chaque mois par rapport à ce point. Chaque estimation est indiquée comme telle. Notre engagement est mesuré uniquement sur ces éléments, selon les conditions du service.",
    "ar": "كيف نقيس: فقط على العبارات المتفق عليها ({k}) والمؤشر المتفق عليه، مقارنة بنقطة بداية تُحفظ اليوم بتاريخها. تقرير كل شهر مقارنة بها. كل تقدير موسوم كتقدير. التزامنا يُقاس على هذه فقط وفق شروط الخدمة.",
    "ru": "Как мы измеряем: только по согласованным фразам ({k}) и согласованному показателю, относительно точки отсчёта, сохранённой сегодня с датой. Отчёт каждый месяц относительно неё. Каждая оценка помечена как оценка. Наше обязательство измеряется только по ним, согласно условиям сервиса.",
}


IMPROVE_SALES = {"he": "יותר מכירות באתר, חודש אחרי חודש", "en": "More sales on the site, month after month",
                 "fr": "Plus de ventes sur le site, mois après mois", "ar": "مزيد من المبيعات في الموقع شهرًا بعد شهر",
                 "ru": "Больше продаж на сайте месяц за месяцем"}
MET_FROM = {"he": "סף 90%:", "en": "90% threshold:", "fr": "seuil 90 % :", "ar": "عتبة 90%:", "ru": "порог 90%:"}


def set_ads_goal(client_id: int) -> dict:
    """google_ads_agent.set_performance_goal, called once the client recorded a
    budget (and by the admin route). Never raises; a refusal is logged with its
    name so the Control Room shows WHY no goal exists."""
    try:
        from agents.google_ads_agent import set_performance_goal
        out = set_performance_goal(client_id)
    except Exception as e:
        out = {"success": False, "error": f"{type(e).__name__}"}
    if not out.get("success"):
        try:
            from agents.client_agent import log_activity
            log_activity(client_id, "google_ads_agent", "ads_goal_not_set",
                         {"error": out.get("error"), "missing": out.get("missing"), "measurement": out.get("measurement")}, {})
        except Exception:
            pass
    return out


def build(aa) -> dict:
    _txt = aa._txt

    def v_keywords(value, client_id):
        return quarter_goals.clean_keywords(value)

    def organic_covered(client_id) -> bool:
        from core import seo_report
        return bool(seo_report.coverage(client_id).get("covered"))

    def prepare(client_id, params, context, rows):
        """Organic: research first, then the approved set is THE keyword list."""
        from core import organic_plan
        lang = (context or {}).get("language") or "he"
        # NO TARGET BEFORE ITS INPUTS (governance §5, core/target_inputs): while a required
        # input is missing there is no card at all — the client reads what is missing.
        now = target_inputs.state(client_id)
        if now["missing"]:
            return {"clarify": target_inputs.missing_text(now, lang)}
        clean = {k: v for k, v in (params or {}).items() if k in ("posts_per_week", "improve")}
        if organic_covered(client_id):
            value = organic_plan.current(client_id)
            step = organic_plan.stage(value)
            if step in ("state", "keywords"):
                return {"switch_to": "advance_organic_plan", "switch_params": {},
                        "notice": organic_plan.next_line(client_id, lang)}
            if step == "approve":
                return {"clarify": organic_plan.next_line(client_id, lang)}
            if step != "ready":
                return {"clarify": organic_plan.next_line(client_id, lang)}
            clean["keywords"] = list(value["approved"]["keywords"])
            strategy = value.get("strategy") or {}
            clean["internal_months"] = strategy.get("internal_months")
            clean["committed_months"] = strategy.get("committed_months")
            if (params or {}).get("positions_aim") not in (None, ""):
                clean["positions_aim"] = params["positions_aim"]
        else:
            clean["keywords"] = []              # no organic in the package: no organic track
        if now.get("kind") == "sales" and not clean.get("improve"):
            clean["improve"] = quarter_goals.pick(IMPROVE_SALES, lang)      # this client counts sales, not enquiries
        if now["thin"]:
            # A thin baseline carries NO result target, whatever the model aimed for:
            # the first period is a learning period, and the card says so
            clean["learning"] = target_inputs.learning(now)
        else:
            # The RESULT target is in what THIS client counts: sales for a store, enquiries otherwise.
            # An aim in the other unit is dropped (a store client gets no enquiries target).
            key = "sales" if now.get("kind") == "sales" else "leads"
            if (params or {}).get(f"{key}_aim") not in (None, ""):
                basis = target_inputs.history_basis(now)
                aim = min(int(float(params[f"{key}_aim"])), target_inputs.history_ceiling(basis)) if basis else 0
                if aim >= 1:                      # never above what the client's own history supports
                    clean[f"{key}_aim"], clean[f"{key}_basis"] = aim, basis
        if rows:
            return {"params": clean, "supersedes": rows[0].get("id")}
        return {"params": clean}

    def validate_stored(client_id, params):
        params = params if isinstance(params, dict) else {}
        out = {"keywords": v_keywords(params.get("keywords") or [], client_id)}
        if params.get("posts_per_week") not in (None, ""):
            out["posts_per_week"] = v_posts(params["posts_per_week"], client_id)
        if params.get("improve"):
            out["improve"] = v_improve(params["improve"], client_id)
        for key in ("internal_months", "committed_months"):
            if params.get(key) not in (None, ""):
                out[key] = max(0.5, min(24.0, float(params[key])))
        if params.get("learning"):
            out["learning"] = v_learning(params["learning"])
        for key in ("leads", "sales"):
            if params.get(f"{key}_aim") in (None, ""):
                continue
            if out.get("learning"):
                raise ValueError("a learning period carries no result target")
            out[f"{key}_aim"] = v_leads(params[f"{key}_aim"], client_id)
            out[f"{key}_basis"] = v_basis(params.get(f"{key}_basis"), key)
        if out.get("leads_aim") and out.get("sales_aim"):
            raise ValueError("one result target: what this client counts")
        if params.get("positions_aim") not in (None, "") and out["keywords"]:
            out["positions_aim"] = v_positions(params["positions_aim"], client_id)
        return out

    def v_basis(value, key="leads"):
        """REQUIRED with a result target: the client's own history, with its figures."""
        value = value if isinstance(value, dict) else {}
        if value.get("kind") != "history":
            raise ValueError("a result target with nothing it rests on")
        out = {"kind": "history", "spend": float(value["spend"]), "budget": float(value["budget"])}
        if key == "sales":
            if value.get("unit") != "sales":
                raise ValueError("a sales target rests on measured sales")
            out.update(unit="sales", orders=int(value["orders"]))
        else:
            out["leads"] = int(value["leads"])
        if target_inputs.thin({"leads": out.get("leads"), "orders": out.get("orders"), "spend": out["spend"]}, key) or out["budget"] <= 0:
            raise ValueError("the history is too thin to carry a result target")
        return out

    def v_learning(value):
        value = value if isinstance(value, dict) else {}
        base = value.get("baseline") if isinstance(value.get("baseline"), dict) else {}
        out = {"baseline": {k: (None if base.get(k) is None else float(base[k])) for k in ("leads", "orders", "revenue", "spend")},
               "budget": float(value["budget"]), "kind": "sales" if value.get("kind") == "sales" else "leads", "min_results": int(value.get("min_results") or target_inputs.MIN_BASELINE_RESULTS),
               "why": [str(w)[:20] for w in (value.get("why") or [])][:4], "until": str(value["until"])[:10]}
        if out["budget"] <= 0 or not re.match(r"^\d{4}-\d{2}-\d{2}$", out["until"]):
            raise ValueError("a learning period needs its budget and its date")
        return out

    def v_leads(value, client_id):
        n = int(float(value))
        if not 1 <= n <= 100000:
            raise ValueError("enquiries aim out of range")
        return n

    def v_positions(value, client_id):
        n = round(float(value), 1)
        if not 1 <= n <= 50:
            raise ValueError("positions aim out of range")
        return n

    def v_posts(value, client_id):
        n = int(value)
        if not 1 <= n <= 7:
            raise ValueError("posts per week out of range")
        return n

    def v_improve(value, client_id):
        text = aa._v_short_text(value, 200)
        if YES_NO_GOAL.search(text):
            raise ValueError("a yes/no goal: express it as positions to gain (positions_aim)")
        return text

    def aims_of(params):
        return {"leads_30d": params.get("leads_aim"), "sales_30d": params.get("sales_aim"),
                "positions_gained": params.get("positions_aim")}

    def bases_of(params):
        return {"leads_30d": params.get("leads_basis"), "sales_30d": params.get("sales_basis")}

    def display(client_id, params):
        lang = aa._lang(client_id)
        words = params.get("keywords") or []
        t = quarter_goals._t
        lines = [f"• {t('social_goal', lang, n=params.get('posts_per_week') or 3)}"]
        if words:
            lines.append(f"• {t('organic_goal', lang, k=', '.join(words))}")
        lines.append(f"• {t('improve_goal', lang, x=params.get('improve') or quarter_goals.pick(quarter_goals.T['improve_default'], lang))}")
        # The numeric targets the click approves: the COMMITTED numbers and the 90% line.
        targets = quarter_goals.build_targets(words, params.get("posts_per_week") or 3, aims_of(params),
                                              bases=bases_of(params))
        lines.append(quarter_goals.pick(quarter_goals.T["targets_title"], lang) + ":")
        for item in targets:
            # §9 point 4: no number without what it rests on — basis_line raises rather than print a bare one
            lines.append(f"• {quarter_goals.pick(quarter_goals.T['m_' + item['metric']], lang)}: "
                         f"{quarter_goals.num(item['target'])} ({quarter_goals.pick(MET_FROM, lang)} "
                         f"{quarter_goals.num(quarter_goals.threshold(item['target']))}) · "
                         f"{target_inputs.basis_line(item['basis'], lang)}")
        if params.get("learning"):
            lines.extend(target_inputs.learning_lines(params["learning"], lang))
        if params.get("committed_months"):
            lines.append(t("estimate", lang, c=quarter_goals.num(params["committed_months"]))
                         + f" ({target_inputs.basis_line({'kind': 'plan'}, lang)})")
        terms = quarter_goals.pick(TERMS, lang).replace("{k}", ", ".join(words) if words else "—")
        return {"lines": "\n".join(lines), "terms": terms}

    def run(client_id, params, background_tasks):
        plan = ({"internal_months": params["internal_months"], "committed_months": params["committed_months"]}
                if params.get("committed_months") else None)
        value = quarter_goals.save(client_id, params.get("keywords") or [], params.get("posts_per_week") or 3,
                                   params.get("improve", ""), plan=plan, aims=aims_of(params),
                                   bases=bases_of(params), learning=params.get("learning"))
        quarter_goals._block_cache.pop(int(client_id), None)
        return {"keywords": len(value["keywords"])}

    # ── record_target_inputs: what the client told us, kept only after their click ──

    input_labels = {
        "budget_ils": _txt("תקציב חודשי לשיווק", "Monthly marketing budget", "Budget marketing mensuel", "ميزانية التسويق الشهرية",
                           "Месячный бюджет на маркетинг"),
        "sells": _txt("מה נמכר", "What is sold", "Ce qui est vendu", "ما الذي يُباع", "Что продаётся"),
        "margin_pct": _txt("מרווח", "Margin", "Marge", "الهامش", "Наценка"),
        "lead_time": _txt("זמן אספקה", "Lead time", "Délai", "مهلة التسليم", "Срок"),
        "capacity": _txt("כמה אפשר לספק", "How much you can supply", "Capacité", "القدرة على التوريد", "Сколько вы можете выполнить"),
        "seasonality": _txt("עונתיות", "Seasonality", "Saisonnalité", "الموسمية", "Сезонность"),
    }
    inputs_done = _txt(
        "נרשם ✓", "Recorded ✓", "Enregistré ✓", "تم التسجيل ✓", "Записано ✓")
    inputs_ready = _txt(
        "עכשיו יש לי את מה שצריך כדי להציע יעדים. כתבו לי \"יעדים\" ואציע.",
        "I now have what it takes to propose targets. Write \"goals\" and I will propose them.",
        "J'ai maintenant ce qu'il faut pour proposer des objectifs. Écrivez « objectifs » et je les propose.",
        "لديّ الآن ما يلزم لاقتراح الأهداف. اكتبوا \"الأهداف\" وسأقترحها.",
        "Теперь у меня есть всё, чтобы предложить цели. Напишите «цели» — и я предложу.")
    inputs_still = _txt("עדיין חסר:", "Still missing:", "Il manque encore :", "ما زال ينقص:", "Ещё не хватает:")

    def v_input_text(limit):
        return lambda value, client_id: aa._v_short_text(value, limit)

    def v_budget(value, client_id):
        return target_inputs.clean({"budget_ils": value})["budget_ils"]

    def v_margin(value, client_id):
        return target_inputs.clean({"margin_pct": value})["margin_pct"]

    def prepare_inputs(client_id, params, context, rows):
        lang = (context or {}).get("language") or "he"
        clean = target_inputs.clean(params)
        if not clean:                      # nothing to record: say what is missing, in words
            return {"clarify": target_inputs.missing_text(target_inputs.state(client_id), lang)}
        if rows:
            return {"params": clean, "supersedes": rows[0].get("id")}
        return {"params": clean}

    def validate_inputs(client_id, params):
        params = params if isinstance(params, dict) else {}
        if set(params) - set(target_inputs.FIELDS):
            raise ValueError("unknown params")
        clean = target_inputs.clean(params)
        if not clean:
            raise ValueError("nothing to record")
        return clean

    def display_inputs(client_id, params):
        lang = aa._lang(client_id)
        lines = []
        for key in target_inputs.FIELDS:
            if params.get(key) in (None, ""):
                continue
            value = (target_inputs._money(params[key], lang) if key == "budget_ils"
                     else f"{params[key]:g}%" if key == "margin_pct" else str(params[key]))
            lines.append(f"• {aa._pick(input_labels[key], lang)}: {value}")
        return {"lines": "\n".join(lines)}

    def run_inputs(client_id, params, background_tasks):
        target_inputs.record(client_id, params, by="client")
        quarter_goals._block_cache.pop(int(client_id), None)
        if params.get("budget_ils") and background_tasks is not None:
            # The budget is what google_ads_agent.set_performance_goal waited for (it had no caller):
            # the internal 90-day target, set after the click. It refuses by name while an input is
            # missing or the account is not measuring — see set_ads_goal.
            background_tasks.add_task(set_ads_goal, client_id)
        return {"missing": target_inputs.missing(client_id)}

    def inputs_done_message(result, lang):
        gaps = result.get("missing") or []
        if not gaps:
            return aa._pick(inputs_done, lang) + " " + aa._pick(inputs_ready, lang)
        return "\n".join([aa._pick(inputs_done, lang) + " " + aa._pick(inputs_still, lang)]
                         + [f"• {target_inputs._pick(target_inputs.NAMES[k], lang)}" for k in gaps if k in target_inputs.NAMES])

    done = _txt(
        "סוכם ✓ היעדים מופיעים עכשיו במסך הבית, עם איפה כל אחד עומד והצעד הבא. נעדכן אותם יחד בכל רגע.",
        "Agreed ✓ The goals now appear on your home page, with where each one stands and the next step. We can update them together any time.",
        "C'est convenu ✓ Les objectifs apparaissent maintenant sur votre page d'accueil, avec l'état de chacun et la prochaine étape. Nous pouvons les ajuster ensemble à tout moment.",
        "تم الاتفاق ✓ تظهر الأهداف الآن في صفحتكم الرئيسية مع موقع كل منها والخطوة التالية. يمكننا تحديثها معًا في أي وقت.",
        "Договорились ✓ Цели теперь на главной странице — с тем, где каждая сейчас, и следующим шагом. Обновим вместе в любой момент.")

    return {
        "agree_quarter_goals": {
            "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
            "does": "THE 3-MONTH GOALS: after the deep characterisation, ONE card with the client's goals for the next "
                    "3 months, EVERY goal a NUMBER (terms §6: 90% of a numeric target counts as met, else the 3 months' "
                    "management fees are refunded) — a steady social presence (posts a week, run by מאיה), organic growth focused ONLY on "
                    "the keywords the client APPROVED in the organic plan, and what must improve. For a package with "
                    "organic the system first runs the organic plan (state → research → their choice → strategy) and "
                    "fills the keywords itself: never type keywords here. posts_per_week from what fits them (3 if "
                    "unsure); leads_aim / positions_aim = your honest aims (code turns them into the committed targets the "
                    "client sees). The click starts the 3-month clock and captures the dated baseline. "
                    "ORDER (target_inputs in your data): while target_inputs.missing is not empty the system shows NO card — "
                    "it tells the client what is missing; get it and record it with record_target_inputs first. When the "
                    "measured baseline is too thin (code decides), the card carries NO result target: it states a learning "
                    "period instead. The result target is in what the client COUNTS: sales_aim for a store client "
                    "(target_inputs.kind = sales), leads_aim otherwise — the other one is dropped. With a thin baseline: a learning "
                    "period instead. Never say a result number yourself in either case.",
            "params": {
                "keywords": {"required": False, "validate": v_keywords,
                             "hint": "leave empty: the system fills the client-approved organic keywords"},
                "posts_per_week": {"required": False, "validate": v_posts, "hint": "1-7, default 3"},
                "improve": {"required": False, "validate": v_improve,
                            "hint": "optional, the client's language: the one thing that must improve (e.g. more calls). "
                                    "Never a yes/no goal like 'page one' — that is positions_aim"},
                "leads_aim": {"required": False, "validate": v_leads,
                              "hint": "your honest aim: enquiries in 30 days at the end of the 3 months (a number). "
                                      "The card shows the client a committed number computed in code"},
                "sales_aim": {"required": False, "validate": v_leads,
                              "hint": "ONLY for a client who counts SALES (target_inputs.kind = sales): your honest aim for "
                                      "paid orders in the store in 30 days at the end of the 3 months (a number). Use this "
                                      "INSTEAD of leads_aim for them; code caps it at what their own history supports"},
                "positions_aim": {"required": False, "validate": v_positions,
                                  "hint": "organic only: your honest aim for the average positions gained on the agreed "
                                          "phrases (a number, e.g. 5). Never 'page one'"},
            },
            "available": lambda state, client: True,
            "prepare": prepare,
            "validate_stored": validate_stored,
            "display": display,
            "run": run,
            "button": _txt("מסכימים על היעדים", "Agree on these goals", "Valider ces objectifs", "نتفق على هذه الأهداف",
                           "Согласовать цели"),
            "title": _txt("היעדים ל-3 החודשים הקרובים", "Goals for the next 3 months", "Objectifs des 3 prochains mois",
                          "أهداف الأشهر الثلاثة القادمة", "Цели на ближайшие 3 месяца"),
            "what": _txt(
                "אלה היעדים שאני מציע לנו ל-3 החודשים הקרובים:\n{lines}\n{terms}\n90% מיעד מספרי ומעלה נחשב עמידה ביעד, כמו בסעיף 6 בתקנון (/terms/#s6). רוצים לשנות משהו? אל תאשרו, כתבו לי מה לשנות.",
                "These are the goals I suggest for us for the next 3 months:\n{lines}\n{terms}\n90% or more of a numeric target counts as met, as in section 6 of the Terms (/terms/#s6). Want something changed? Don't approve; tell me what to change.",
                "Voici les objectifs que je propose pour les 3 prochains mois :\n{lines}\n{terms}\n90 % ou plus d'un objectif chiffré compte comme atteint, comme à l'article 6 des Conditions (/terms/#s6). Quelque chose à changer ? Ne validez pas ; dites-moi quoi modifier.",
                "هذه الأهداف التي أقترحها لنا للأشهر الثلاثة القادمة:\n{lines}\n{terms}\n90% أو أكثر من الهدف الرقمي يُعدّ تحقيقًا له، كما في البند 6 من الشروط (/terms/#s6). تريدون تغيير شيء؟ لا توافقوا؛ اكتبوا لي ما المطلوب تغييره.",
                "Вот цели, которые я предлагаю на ближайшие 3 месяца:\n{lines}\n{terms}\n90% и более от числовой цели считается её достижением — как в разделе 6 Условий (/terms/#s6). Хотите что-то изменить? Не одобряйте — напишите, что поменять."),
            "done": done,
        },
        "record_target_inputs": {
            "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
            "does": "RECORD WHAT TARGETS NEED: when the client TELLS you their monthly marketing budget, what they sell and at "
                    "what margin, their lead time, how much they can supply or their seasonality — put exactly what they said "
                    "on this card; their click records it. Propose it whenever target_inputs.missing names something the "
                    "client just gave you. Only the client's own words and numbers: never a figure you estimated.",
            "params": {
                "budget_ils": {"required": False, "validate": v_budget,
                               "hint": "the monthly marketing budget in ILS, a number, exactly as the client said it"},
                "sells": {"required": False, "validate": v_input_text(200), "hint": "what is sold, the client's words"},
                "margin_pct": {"required": False, "validate": v_margin, "hint": "the margin in percent (a number between 0 and 100)"},
                "lead_time": {"required": False, "validate": v_input_text(120), "hint": "e.g. 15-25 business days"},
                "capacity": {"required": False, "validate": v_input_text(160), "hint": "how many orders / clients they can handle"},
                "seasonality": {"required": False, "validate": v_input_text(160), "hint": "strong and weak months"},
            },
            "available": lambda state, client: True,
            "prepare": prepare_inputs,
            "validate_stored": validate_inputs,
            "display": display_inputs,
            "run": run_inputs,
            "button": _txt("נכון, לרשום", "Correct, record it", "C'est exact, enregistrer", "صحيح، سجّلوا", "Верно, записать"),
            "title": _txt("הנתונים שעליהם נבנה היעדים", "The figures the targets are built on", "Les données sur lesquelles reposent les objectifs",
                          "المعطيات التي تُبنى عليها الأهداف", "Данные, на которых строятся цели"),
            "what": _txt(
                "זה מה שהבנתי מכם. אשרו רק אם זה מדויק, כי על זה ייבנו היעדים:\n{lines}\nמשהו לא מדויק? אל תאשרו, כתבו לי מה לתקן.",
                "This is what I understood from you. Approve only if it is accurate, because the targets will be built on it:\n{lines}\nSomething off? Don't approve; tell me what to correct.",
                "Voici ce que j'ai compris. Validez seulement si c'est exact, car les objectifs reposeront dessus :\n{lines}\nUne erreur ? Ne validez pas ; dites-moi quoi corriger.",
                "هذا ما فهمته منكم. وافقوا فقط إن كان دقيقًا، لأن الأهداف ستُبنى عليه:\n{lines}\nشيء غير دقيق؟ لا توافقوا؛ اكتبوا لي ما يجب تصحيحه.",
                "Вот что я понял. Подтвердите, только если всё точно — на этом будут строиться цели:\n{lines}\nЧто-то неточно? Не подтверждайте — напишите, что исправить."),
            "done": inputs_done,
            "done_message": inputs_done_message,
        },
    }
