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

from core import quarter_goals

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


MET_FROM = {"he": "סף 90%:", "en": "90% threshold:", "fr": "seuil 90 % :", "ar": "عتبة 90%:", "ru": "порог 90%:"}


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
        if (params or {}).get("leads_aim") not in (None, ""):
            clean["leads_aim"] = params["leads_aim"]
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
        if params.get("leads_aim") not in (None, ""):
            out["leads_aim"] = v_leads(params["leads_aim"], client_id)
        if params.get("positions_aim") not in (None, "") and out["keywords"]:
            out["positions_aim"] = v_positions(params["positions_aim"], client_id)
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
        return {"leads_30d": params.get("leads_aim"), "positions_gained": params.get("positions_aim")}

    def display(client_id, params):
        lang = aa._lang(client_id)
        words = params.get("keywords") or []
        t = quarter_goals._t
        lines = [f"• {t('social_goal', lang, n=params.get('posts_per_week') or 3)}"]
        if words:
            lines.append(f"• {t('organic_goal', lang, k=', '.join(words))}")
        lines.append(f"• {t('improve_goal', lang, x=params.get('improve') or quarter_goals.pick(quarter_goals.T['improve_default'], lang))}")
        # The numeric targets the click approves: the COMMITTED numbers and the 90% line.
        targets = quarter_goals.build_targets(words, params.get("posts_per_week") or 3, aims_of(params))
        lines.append(quarter_goals.pick(quarter_goals.T["targets_title"], lang) + ":")
        for item in targets:
            lines.append(f"• {quarter_goals.pick(quarter_goals.T['m_' + item['metric']], lang)}: "
                         f"{quarter_goals.num(item['target'])} ({quarter_goals.pick(MET_FROM, lang)} "
                         f"{quarter_goals.num(quarter_goals.threshold(item['target']))})")
        if params.get("committed_months"):
            lines.append(t("estimate", lang, c=quarter_goals.num(params["committed_months"])))
        terms = quarter_goals.pick(TERMS, lang).replace("{k}", ", ".join(words) if words else "—")
        return {"lines": "\n".join(lines), "terms": terms}

    def run(client_id, params, background_tasks):
        plan = ({"internal_months": params["internal_months"], "committed_months": params["committed_months"]}
                if params.get("committed_months") else None)
        value = quarter_goals.save(client_id, params.get("keywords") or [], params.get("posts_per_week") or 3,
                                   params.get("improve", ""), plan=plan, aims=aims_of(params))
        quarter_goals._block_cache.pop(int(client_id), None)
        return {"keywords": len(value["keywords"])}

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
                    "client sees). The click starts the 3-month clock and captures the dated baseline.",
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
    }
