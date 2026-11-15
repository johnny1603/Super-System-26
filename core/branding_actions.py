"""The branding cards (core/branding) — registered into agent_actions.ACTIONS.

Four cards, one decision each:
- `create_brand_mockups`      ליאור proposes it; the click runs ONE attempt.
- `approve_branding`          opened by code under a finished attempt: the
                              preview link, and "this is my direction".
- `request_logo_designer`     the logo with our human designer instead.
- `approve_production_quote`  opened by code when staff send a quote: the
                              price of real production, approved BEFORE it.
- `upgrade_to_branding`       a package without the content & media service:
                              ONE card that explains the service; the click
                              prepares the upgrade offer through the existing
                              pricing path. No characterisation before it.

`build(aa)` gets the agent_actions module itself, so this file imports
nothing from it at import time (agent_actions imports us).
"""
from core import branding

LANGS = ("he", "en", "fr", "ar", "ru")


def build(aa) -> dict:
    _txt, ActionFailed = aa._txt, aa.ActionFailed

    def _pick(texts, lang):          # agent_actions defines _pick after ACTIONS — looked up at call time
        return aa._pick(texts, lang)

    def _with(table: dict, *more) -> dict:
        """One 5-language text + fixed lines appended to each language."""
        return {lang: " ".join([table[lang]] + [branding.text(m, lang).replace("{free}", str(branding.FREE_ATTEMPTS))
                                                for m in more]) for lang in LANGS}

    refusals = {
        "attempts_used": _txt(
            "השתמשתם בכל ניסיונות ההדמיה שבחינם. ניסיון נוסף כרוך בסכום סמלי, והאפשרות הזאת עוד לא פתוחה אצלנו, אז לא הרצתי כלום ועדכנתי את הצוות. בינתיים אפשר לאשר אחד מהכיוונים שראיתם, או לעצב את הלוגו מול המעצב האנושי שלנו.",
            "You've used all the free mockup attempts. An extra attempt carries a small symbolic charge, and that option isn't open with us yet, so I ran nothing and let the team know. Meanwhile you can approve one of the directions you saw, or have the logo designed with our human designer.",
            "Vous avez utilisé tous les essais gratuits. Un essai supplémentaire est payant (somme symbolique) et cette option n'est pas encore ouverte chez nous : je n'ai rien lancé et j'ai prévenu l'équipe. En attendant, vous pouvez valider une des directions vues, ou faire concevoir le logo avec notre designer humain.",
            "استخدمتم كل المحاولات المجانية. المحاولة الإضافية بمبلغ رمزي، وهذا الخيار غير مفتوح لدينا بعد، لذلك لم أنفّذ شيئًا وأبلغت الفريق. في الأثناء يمكن اعتماد أحد الاتجاهات التي رأيتموها، أو تصميم الشعار مع مصممنا البشري.",
            "Вы использовали все бесплатные попытки. Дополнительная попытка стоит символическую сумму, и эта опция у нас пока не открыта — я ничего не запускал и сообщил команде. Пока можно утвердить одно из увиденных направлений или сделать логотип с нашим дизайнером-человеком."),
        "already_approved": _txt(
            "כבר אישרתם כיוון מיתוג, אז ניסיונות ההדמיה סגורים. מכאן ממשיכים לייצור: תגידו לי מה תרצו להפיק ותקבלו מחיר לאישור לפני שמתחילים.",
            "You already approved a branding direction, so the mockup attempts are closed. From here it's production: tell me what you'd like produced and you'll get a price to approve before anything starts.",
            "Vous avez déjà validé une direction : les essais sont clos. Place à la production : dites-moi ce que vous voulez produire et vous recevrez un prix à valider avant tout démarrage.",
            "لقد اعتمدتم اتجاهًا بالفعل، لذا أُغلقت المحاولات. من هنا ننتقل إلى الإنتاج: قولوا لي ما تريدون إنتاجه وستحصلون على سعر للموافقة قبل البدء.",
            "Вы уже утвердили направление, поэтому попытки закрыты. Дальше — производство: скажите, что изготовить, и вы получите цену на одобрение до начала работ."),
        "design_failed": _txt(
            "העיצוב לא הצליח הפעם, והניסיון הזה לא נספר לכם. כתבו לי \"נסה שוב\" ואריץ מחדש.",
            "The design didn't come through this time, and this attempt was not counted. Write \"try again\" and I'll rerun it.",
            "La création n'a pas abouti cette fois, et cet essai n'a pas été compté. Écrivez « réessaie » et je relance.",
            "لم ينجح التصميم هذه المرة، ولم تُحتسب هذه المحاولة. اكتبوا \"حاول مجددًا\" وسأعيد التنفيذ.",
            "В этот раз дизайн не получился, и попытка не засчитана. Напишите «ещё раз» — запущу снова."),
        "cost_cap": _txt(
            "לא הרצתי את הניסיון הזה כי הוא היה חורג מהתקרה שקבענו לניסיון אחד. הוא לא נספר לכם, והצוות קיבל התראה.",
            "I didn't run this attempt because it would have passed the limit we set for a single attempt. It was not counted, and the team was alerted.",
            "Je n'ai pas lancé cet essai : il aurait dépassé la limite fixée pour un essai. Il n'a pas été compté et l'équipe est prévenue.",
            "لم أنفّذ هذه المحاولة لأنها كانت ستتجاوز الحد المحدد للمحاولة الواحدة. لم تُحتسب وتم تنبيه الفريق.",
            "Я не запустил эту попытку: она превысила бы лимит на одну попытку. Она не засчитана, команда уведомлена."),
        "attempt_missing": _txt(
            "לא מצאתי את ההדמיה הזאת, אז לא אישרתי כלום. כתבו לי ואכין כיוון חדש.",
            "I couldn't find that mockup, so nothing was approved. Write to me and I'll prepare a new direction.",
            "Je n'ai pas retrouvé cette maquette : rien n'a été validé. Écrivez-moi et je prépare une nouvelle direction.",
            "لم أجد هذا النموذج، لذلك لم يُعتمد شيء. اكتبوا لي وسأجهّز اتجاهًا جديدًا.",
            "Я не нашёл этот макет, ничего не утверждено. Напишите — подготовлю новое направление."),
        "package_unreadable": _txt(
            "כרגע אני לא מצליח לראות מה כלול בחבילה שלכם, אז לא התחלתי. נסו שוב בעוד כמה דקות.",
            "I can't see what your package includes right now, so I didn't start. Please try again in a few minutes.",
            "Je ne parviens pas à voir ce que comprend votre formule pour l'instant ; je n'ai donc rien lancé. Réessayez dans quelques minutes.",
            "لا أستطيع الآن رؤية ما تتضمنه باقتكم، لذلك لم أبدأ. حاولوا مجددًا بعد دقائق.",
            "Сейчас я не вижу, что входит в ваш пакет, поэтому не начал. Попробуйте через несколько минут."),
        "already_covered": _txt(
            "שירות התוכן והמדיה כבר כלול בחבילה שלכם, אז אין מה לשדרג. אפשר להתחיל את המיתוג עכשיו.",
            "The content and media service is already in your package, so there is nothing to upgrade. You can start the branding now.",
            "Le service contenus et médias est déjà dans votre formule : rien à ajouter. Vous pouvez commencer l'identité maintenant.",
            "خدمة المحتوى والوسائط مشمولة بالفعل في باقتكم، فلا حاجة للترقية. يمكنكم بدء الهوية الآن.",
            "Услуга контента и медиа уже в вашем пакете, расширять нечего. Можно начинать брендинг."),
        "proposal_failed": _txt(
            "לא הצלחתי להכין את ההצעה הפעם, והצוות קיבל התראה וישלח לכם אותה.",
            "I couldn't prepare the offer this time; the team was alerted and will send it to you.",
            "Je n'ai pas pu préparer l'offre cette fois ; l'équipe est prévenue et vous l'enverra.",
            "لم أتمكن من تجهيز العرض هذه المرة؛ تم تنبيه الفريق وسيرسلونه إليكم.",
            "В этот раз предложение не подготовилось; команда уведомлена и пришлёт его вам."),
        "no_open_quote": _txt(
            "הצעת המחיר הזאת כבר לא פתוחה, אז לא אישרתי כלום.",
            "That quote is no longer open, so nothing was approved.",
            "Ce devis n'est plus ouvert : rien n'a été validé.",
            "عرض السعر هذا لم يعد مفتوحًا، لذلك لم يُعتمد شيء.",
            "Это предложение уже не актуально, ничего не одобрено."),
    }

    # ── create_brand_mockups ────────────────────────────────────────────────
    def v_direction(value, client_id):
        return aa._v_short_text(value, branding.MAX_DIRECTION_CHARS)

    def prepare_create(client_id, params, context, rows):
        lang = (context or {}).get("language") or "he"
        cover = branding.coverage(client_id)
        if not cover["readable"]:
            return {"refuse": _pick(refusals["package_unreadable"], lang)}
        if not cover["covered"]:
            # Never a characterisation or an attempt outside the package: the ONE upgrade card
            return {"switch_to": "upgrade_to_branding", "switch_params": {},
                    "notice": branding.text(branding.NOT_COVERED, lang)}
        value = branding.state(client_id)
        code = branding.can_attempt(value)
        if code:
            if code == "attempts_used" and not value.get("limit_alerted"):
                value["limit_alerted"] = branding._now()
                branding._save(client_id, value)
                aa._alert(f"client {client_id}: used all {branding.FREE_ATTEMPTS} free branding attempts and asked "
                          "for another. The extra-attempt charge is OFF (core/branding."
                          "EXTRA_ATTEMPT_CHARGE_ENABLED) — to let them go on, open more attempts in the Control Room: the client's page, section מיתוג.")
            return {"refuse": _pick(refusals[code], lang)}
        clean = {"direction": params["direction"]}
        for row in rows:
            if (row.get("params") or {}) == clean:
                return {"existing": row}
        return {"params": clean, "supersedes": rows[0].get("id") if rows else None}

    def validate_create(client_id, params):
        params = params if isinstance(params, dict) else {}
        if set(params) - {"direction"}:
            raise ValueError("unknown params")
        return {"direction": v_direction(params.get("direction"), client_id)}

    def display_create(client_id, params):
        try:
            value = branding.state(client_id)
            return {"n": branding.attempts_used(value) + 1, "free": branding.FREE_ATTEMPTS,
                    "direction": params.get("direction", "")}
        except Exception:
            return {"n": "", "free": branding.FREE_ATTEMPTS, "direction": params.get("direction", "")}

    def run_create(client_id, params, background_tasks, row=None):
        row = row or {}
        try:
            result = branding.create_attempt(client_id, params["direction"], lang=row.get("language") or "he")
        except branding.BrandingError as e:
            if e.code == "cost_cap":
                aa._alert(f"client {client_id}: a branding attempt was refused by the per-attempt cost cap")
            raise ActionFailed(e.code if e.code in refusals else "error", str(e))
        out = {"attempt": result["n"], "left": result["left"]}
        follow = aa.propose(client_id, row.get("origin") or "client_chat", row.get("persona") or "media",
                            {"name": "approve_branding", "params": {"attempt": result["n"]}}, by_code=True)
        if follow.get("card"):
            out["next_card"] = follow["card"]
        return out

    created = _txt(
        "ההדמיות מוכנות 🎨 בכרטיס שמתחת יש קישור לראות את כולן. קחו רגע להרגיש אם זה אתם. נשארו לכם {left} ניסיונות בחינם.",
        "The mockups are ready 🎨 The card below has a link to see them all. Take a moment to feel whether this is you. You have {left} free attempts left.",
        "Les maquettes sont prêtes 🎨 La carte ci-dessous contient un lien pour toutes les voir. Prenez un instant pour sentir si c'est vous. Il vous reste {left} essais gratuits.",
        "النماذج جاهزة 🎨 في البطاقة أدناه رابط لرؤيتها كلها. خذوا لحظة لتشعروا إن كانت تشبهكم. بقي لكم {left} محاولات مجانية.",
        "Макеты готовы 🎨 В карточке ниже ссылка, чтобы посмотреть все. Почувствуйте, ваше ли это. Осталось бесплатных попыток: {left}.")

    # ── approve_branding ────────────────────────────────────────────────────
    def v_attempt(value, client_id):
        n = int(value)
        if n < 1 or not branding.attempt(client_id, n):
            raise ValueError("no such attempt")
        return n

    def run_approve(client_id, params, background_tasks):
        try:
            branding.approve(client_id, params["attempt"])
        except branding.BrandingError as e:
            raise ActionFailed(e.code if e.code in refusals else "error", str(e))
        aa._alert(f"client {client_id}: approved a branding direction (attempt {params['attempt']}). The brand "
                  "kit is saved. When they say what to produce, send the production quote from the admin.")
        return {"attempt": params["attempt"]}

    # ── request_logo_designer ───────────────────────────────────────────────
    def run_designer(client_id, params, background_tasks):
        branding.request_designer(client_id)
        return {}

    # ── approve_production_quote ────────────────────────────────────────────
    def v_quote(value, client_id):
        stamp = str(value or "")
        current = branding.quote(client_id)
        if not stamp or current.get("at") != stamp or current.get("status") != "sent":
            raise ValueError("no such open quote")
        return stamp

    def display_quote(client_id, params):
        current = branding.quote(client_id)
        lang = aa._lang(client_id)
        return {"lines": branding.quote_lines(current, lang), "total": current.get("total_ils", ""),
                "note": current.get("note", "")}

    def run_quote(client_id, params, background_tasks):
        try:
            current = branding.approve_quote(client_id)
        except branding.BrandingError as e:
            raise ActionFailed(e.code if e.code in refusals else "error", str(e))
        return {"total": current.get("total_ils")}

    # ── upgrade_to_branding ─────────────────────────────────────────────────
    def branding_missing(state, client):
        package = (state or {}).get("package") or {}
        return bool(package.get("readable")) and not branding.covered(package.get("services") or [])

    def run_upgrade(client_id, params, background_tasks):
        if branding.coverage(client_id)["covered"]:
            raise ActionFailed("already_covered", "content & media is already in the package")
        from agents import support_agent
        client = aa._client(client_id)
        try:
            reply = support_agent._build_upgrade_proposal(
                client_id, client, support_agent._latest_lead(client_id),
                "Add the content and media service (visual content, filming kits and branding mockups) "
                "to my current package.")
        except Exception as e:
            aa._alert(f"client {client_id}: asked to add the content & media service (for branding) and the "
                      f"upgrade proposal failed to build ({type(e).__name__}) — send them the offer by hand")
            raise ActionFailed("proposal_failed", f"{type(e).__name__}: {e}")
        return {"reply": str(reply or "")}

    upgrade_ready = _txt("הכנתי את הצעת השדרוג. היא כאן למטה.", "I prepared the upgrade offer. It is right below.",
                         "J'ai préparé l'offre de mise à niveau. Elle est juste en dessous.", "جهّزت عرض الترقية. تجدونه أدناه.",
                         "Я подготовил предложение по расширению. Оно ниже.")

    return {
        "upgrade_to_branding": {
            "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
            "does": "BRANDING for a client whose package does not include the content & media service "
                    "(branding.covered_by_package false): ONE card explaining what the service includes; the click "
                    "prepares the priced upgrade offer. Propose it instead of any branding question or attempt.",
            "params": {},
            "available": branding_missing,
            "run": run_upgrade,
            "slow": {"minutes": 4, "eta": _txt(
                "הכנת ההצעה לוקחת בדרך כלל כדקה.", "Preparing the offer usually takes about a minute.",
                "La préparation de l'offre prend en général une minute environ.", "تجهيز العرض يستغرق عادةً نحو دقيقة.",
                "Подготовка предложения обычно занимает около минуты.")},
            "button": _txt("להכין לי הצעת שדרוג", "Prepare my upgrade offer", "Préparer mon offre", "جهّزوا لي عرض الترقية",
                           "Подготовить предложение"),
            "title": _txt("מיתוג לעסק: מה השירות כולל", "Branding for your business: what the service includes",
                          "L'identité de votre entreprise : ce que comprend le service",
                          "هوية لعملكم: ما تتضمنه الخدمة", "Брендинг для бизнеса: что входит в услугу"),
            "what": _txt(
                "שירות התוכן והמדיה של ליאור כולל: תמונות וסרטונים לעסק, ערכות צילום עם תסריט והדרכה, ותהליך מיתוג שבו אתם רואים איך העסק נראה תחת המיתוג הכי טוב שלו, בהדמיות: לוגו, כרטיס ביקור, שילוט, פלייר, פוסטר ואיך אתר יכול להיראות. 5 ניסיונות הדמיה בחינם; אחרי שתאשרו כיוון, הייצור הסופי מקבל הצעת מחיר נפרדת. השירות לא כלול כרגע בחבילה שלכם. הלחיצה כאן רק מכינה לכם הצעת שדרוג מסודרת עם המחיר; שום דבר לא מחויב לפני שאישרתם אותה.",
                "Lior's content and media service includes: images and videos for your business, filming kits with a script and coaching, and a branding process where you see how your business looks under its best branding, as mockups: logo, business card, signage, flyer, poster and how a website could look. 5 mockup attempts are free; once you approve a direction, final production gets its own quote. The service isn't in your package right now. This click only prepares an orderly upgrade offer with the price; nothing is charged before you approve it.",
                "Le service contenus et médias de Lior comprend : images et vidéos pour votre entreprise, kits de tournage avec script et coaching, et un processus d'identité où vous voyez votre entreprise sous sa meilleure image, en maquettes : logo, carte de visite, enseigne, flyer, affiche et un aperçu de site. 5 essais gratuits ; une fois une direction validée, la production finale fait l'objet d'un devis séparé. Ce service n'est pas dans votre formule pour l'instant. Ce clic prépare seulement une offre avec le prix ; rien n'est facturé avant votre validation.",
                "تشمل خدمة المحتوى والوسائط لدى ليئور: صورًا وفيديوهات لعملكم، عدّة تصوير مع نص وتوجيه، وعملية هوية ترون فيها كيف يبدو عملكم بأفضل هوية له، كنماذج: شعار، بطاقة عمل، لافتة، منشور، ملصق وكيف يمكن أن يبدو الموقع. 5 محاولات مجانية؛ بعد اعتماد اتجاه يحصل الإنتاج النهائي على عرض سعر منفصل. الخدمة غير مشمولة في باقتكم حاليًا. هذه النقرة تجهّز عرض ترقية بالسعر فقط؛ لا يُخصم شيء قبل موافقتكم.",
                "Услуга контента и медиа Лиора включает: изображения и видео для бизнеса, съёмочные наборы со сценарием и подсказками, и брендинг, где вы видите свой бизнес в лучшем образе на макетах: логотип, визитка, вывеска, флаер, постер и вид сайта. 5 бесплатных попыток; после утверждения направления финальное производство оценивается отдельно. Сейчас услуги нет в вашем пакете. Это нажатие только готовит предложение с ценой; ничего не списывается до вашего одобрения."),
            "done": upgrade_ready,
            "done_message": lambda result, lang: result.get("reply") or _pick(upgrade_ready, lang),
            "refusals": {k: refusals[k] for k in ("already_covered", "proposal_failed")},
        },
        "create_brand_mockups": {
            "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
            "needs_row": True,
            "does": "BRANDING: designs ONE branding direction for the client's business and shows it as MOCKUPS "
                    "(logo, business card, shop sign, flyer, poster, and how a website could look). Mockups only — "
                    "nothing is printed, ordered or built. Propose it only after the client reacted to the direction "
                    "you suggested (see BRANDING in your notes) or asked for a change to an earlier attempt. "
                    "branding.can_try_now false → do not propose it; say why from branding.",
            "params": {"direction": {"required": True, "validate": v_direction,
                                     "hint": "one or two sentences in the client's language: the colours feeling, the "
                                             "style, the kind of logo and anything they asked to change — from what "
                                             "they agreed to in this chat"}},
            "available": lambda state, client: True,
            "prepare": prepare_create,
            "validate_stored": validate_create,
            "display": display_create,
            "run": run_create,
            "slow": {"minutes": 3, "eta": _txt(
                "עיצוב כיוון מיתוג לוקח בדרך כלל כדקה.", "Designing a branding direction usually takes about a minute.",
                "Concevoir une direction de marque prend en général une minute environ.",
                "تصميم اتجاه للهوية يستغرق عادةً نحو دقيقة.", "Разработка направления обычно занимает около минуты.")},
            "title": _txt("הדמיית מיתוג לעסק", "Branding mockups for your business", "Maquettes d'identité pour votre entreprise",
                          "نماذج هوية لعملكم", "Макеты брендинга для вашего бизнеса"),
            "what": _with(_txt(
                "אעצב כיוון מיתוג אחד ואראה לכם אותו בהדמיות: לוגו, כרטיס ביקור, שילוט, פלייר, פוסטר, ואיך אתר יכול להיראות. הכיוון: \"{direction}\". זה ניסיון {n} מתוך {free} בחינם. אלו הדמיות בלבד.",
                "I'll design one branding direction and show it to you as mockups: logo, business card, signage, flyer, poster, and how a website could look. The direction: \"{direction}\". This is attempt {n} of {free} free ones. These are mockups only.",
                "Je conçois une direction de marque et vous la montre en maquettes : logo, carte de visite, enseigne, flyer, affiche et un aperçu de site. La direction : « {direction} ». C'est l'essai {n} sur {free} gratuits. Ce ne sont que des maquettes.",
                "سأصمّم اتجاهًا واحدًا للهوية وأعرضه عليكم كنماذج: شعار، بطاقة عمل، لافتة، منشور، ملصق، وكيف يمكن أن يبدو الموقع. الاتجاه: \"{direction}\". هذه المحاولة {n} من {free} مجانية. هذه نماذج فقط.",
                "Я разработаю одно направление и покажу его на макетах: логотип, визитка, вывеска, флаер, постер и вид сайта. Направление: «{direction}». Это попытка {n} из {free} бесплатных. Это только макеты."),
                branding.TERMS),
            "done": created,
            "done_message": lambda result, lang: _pick(created, lang).replace("{left}", str(result.get("left", ""))),
            "refusals": {k: refusals[k] for k in ("attempts_used", "already_approved", "design_failed", "cost_cap",
                                                  "package_unreadable")},
        },
        "approve_branding": {
            "approver": "client", "client_requestable": False, "internal_only": False, "kind": "execute",
            "code_only": True,
            "does": "Shown by the system under a finished branding attempt: the preview link and the approval.",
            "params": {"attempt": {"required": True, "validate": v_attempt, "hint": ""}},
            "available": lambda state, client: False,
            "details": lambda client_id, params: {"preview_url": branding.view_path(params.get("attempt") or 0)},
            "display": lambda client_id, params: {"n": params.get("attempt", "")},
            "run": run_approve,
            "button": _txt("זה הכיוון שלי", "This is my direction", "C'est ma direction", "هذا اتجاهي", "Это моё направление"),
            "title": _txt("הדמיות המיתוג שלכם", "Your branding mockups", "Vos maquettes d'identité", "نماذج هويتكم",
                          "Ваши макеты брендинга"),
            "what": _txt(
                "ניסיון {n}. פתחו את ההדמיות וקחו רגע. מיתוג הוא לא כמו פרסום: בפרסום אנחנו מובילים, אבל עם המיתוג אתם חיים כל יום, אז הוא צריך להרגיש שלכם. מתחברים? אשרו, והצבעים, הגופנים והסגנון יישמרו כערכת המותג שלכם וישמשו בדפי הנחיתה, בסרטונים ובפוסטים. אחרי האישור אין ניסיונות נוספים, וייצור אמיתי מקבל הצעת מחיר שתאשרו מראש. לא מתחברים? אל תאשרו. כתבו לי מה לא יושב ואנסה כיוון אחר, או בקשו לעצב את הלוגו מול המעצב האנושי שלנו.",
                "Open the mockups and take a moment. Branding is not like advertising: in advertising we lead, but the brand is something you live with every day, so it has to feel like yours. Connect with it? Approve, and the colours, fonts and style are saved as your brand kit and used in your landing pages, videos and posts. After approval there are no more attempts, and real production gets a quote you approve first. Don't connect with it? Don't approve. Tell me what doesn't sit well and I'll try another direction, or ask to have the logo designed with our human designer.",
                "Ouvrez les maquettes et prenez un instant. Une identité n'est pas une publicité : en publicité nous menons, mais votre marque, vous la vivez chaque jour ; elle doit vous ressembler. Elle vous parle ? Validez : couleurs, polices et style deviennent votre kit de marque, utilisé dans vos pages, vidéos et publications. Après validation il n'y a plus d'essais, et la production réelle fait l'objet d'un devis que vous approuvez d'abord. Elle ne vous parle pas ? Ne validez pas. Dites-moi ce qui cloche et j'essaie une autre direction, ou demandez le logo avec notre designer humain.",
                "افتحوا النماذج وخذوا لحظة. الهوية ليست كالإعلان: في الإعلان نحن نقود، أما الهوية فتعيشون معها كل يوم، لذا يجب أن تشعروا أنها لكم. أعجبتكم؟ وافقوا، فتُحفظ الألوان والخطوط والأسلوب كعدّة علامتكم وتُستخدم في صفحات الهبوط والفيديوهات والمنشورات. بعد الموافقة لا محاولات إضافية، والإنتاج الفعلي يأتي بعرض سعر توافقون عليه أولًا. لم تعجبكم؟ لا توافقوا. اكتبوا لي ما الذي لا يناسب وسأجرّب اتجاهًا آخر، أو اطلبوا تصميم الشعار مع مصممنا البشري.",
                "Откройте макеты и не спешите. Бренд — не реклама: в рекламе ведём мы, а с брендом вы живёте каждый день, он должен ощущаться вашим. Откликается? Утвердите — цвета, шрифты и стиль сохранятся как ваш бренд-кит и будут использоваться в лендингах, видео и постах. После утверждения новых попыток нет, а на реальное производство вы получите цену и одобрите её заранее. Не откликается? Не утверждайте. Напишите, что не так, и я попробую другое направление, или попросите сделать логотип с нашим дизайнером-человеком."),
            "done": _txt(
                "נשמר ✓ זו ערכת המותג שלכם מעכשיו, ודפי הנחיתה, הסרטונים והפוסטים ישתמשו בה. מה תרצו להפיק באמת: קבצי לוגו סופיים, כרטיסי ביקור, שילוט, פליירים או פוסטרים? כתבו לי, ותקבלו הצעת מחיר לאישור לפני שמשהו מיוצר.",
                "Saved ✓ This is your brand kit from now on, and your landing pages, videos and posts will use it. What would you like actually produced: final logo files, business cards, signage, flyers or posters? Tell me, and you'll get a quote to approve before anything is made.",
                "Enregistré ✓ C'est désormais votre kit de marque ; vos pages, vidéos et publications l'utiliseront. Que souhaitez-vous réellement produire : fichiers finaux du logo, cartes de visite, enseigne, flyers ou affiches ? Dites-le-moi et vous recevrez un devis à valider avant toute fabrication.",
                "تم الحفظ ✓ هذه عدّة علامتكم من الآن، وستستخدمها صفحات الهبوط والفيديوهات والمنشورات. ماذا تريدون إنتاجه فعلًا: ملفات الشعار النهائية، بطاقات عمل، لافتة، منشورات أم ملصقات؟ اكتبوا لي وستحصلون على عرض سعر للموافقة قبل أي تصنيع.",
                "Сохранено ✓ Теперь это ваш бренд-кит, его будут использовать лендинги, видео и посты. Что изготовить на самом деле: финальные файлы логотипа, визитки, вывеску, флаеры или постеры? Напишите — и получите цену на одобрение до начала изготовления."),
            "cancelled": _txt(
                "בסדר גמור, לא אישרתי כלום. כתבו לי מה לא ישב לכם ואנסה כיוון אחר.",
                "No problem, nothing was approved. Tell me what didn't sit well and I'll try another direction.",
                "Aucun souci, rien n'a été validé. Dites-moi ce qui ne convenait pas et j'essaie une autre direction.",
                "لا مشكلة، لم يُعتمد شيء. اكتبوا لي ما الذي لم يناسبكم وسأجرّب اتجاهًا آخر.",
                "Хорошо, ничего не утверждено. Напишите, что не подошло, и я попробую другое направление."),
            "refusals": {k: refusals[k] for k in ("already_approved", "attempt_missing")},
        },
        "request_logo_designer": {
            "approver": "client", "client_requestable": True, "internal_only": False, "kind": "execute",
            "does": "BRANDING: asks our HUMAN designer to design the client's logo directly with them. Propose it when "
                    "the client does not connect with the logo in the mockups, or asks for a person to design it. The "
                    "designer gives the price; never quote one yourself.",
            "params": {},
            "available": lambda state, client: True,
            "run": run_designer,
            "title": _txt("לוגו מול מעצב אנושי", "A logo with a human designer", "Un logo avec un designer humain",
                          "شعار مع مصمم بشري", "Логотип с дизайнером-человеком"),
            "what": _txt(
                "אעביר את הבקשה למעצב האנושי שעובד איתנו במערכת, והוא ייצור איתכם קשר כדי לעצב את הלוגו ישירות מולכם. את המחיר תקבלו ממנו, לפני שמתחילה עבודה.",
                "I'll pass the request to the human designer who works with us in the system, and they'll contact you to design the logo directly with you. You get the price from them, before any work starts.",
                "Je transmets la demande au designer humain qui travaille avec nous ; il vous contactera pour concevoir le logo directement avec vous. Le prix vous est donné par lui, avant tout travail.",
                "سأحوّل الطلب إلى المصمم البشري الذي يعمل معنا، وسيتواصل معكم لتصميم الشعار مباشرة معكم. السعر تحصلون عليه منه قبل بدء أي عمل.",
                "Я передам запрос дизайнеру-человеку, который работает с нами, и он свяжется с вами, чтобы сделать логотип напрямую. Цену вы получите от него до начала работы."),
            "done": _txt("הועבר ✓ המעצב שלנו ייצור איתכם קשר.", "Passed on ✓ Our designer will contact you.",
                         "Transmis ✓ Notre designer vous contactera.", "تم التحويل ✓ سيتواصل معكم مصممنا.",
                         "Передано ✓ Наш дизайнер свяжется с вами."),
        },
        "approve_production_quote": {
            "approver": "client", "client_requestable": False, "internal_only": False, "kind": "execute",
            "code_only": True,
            "does": "Shown by the system when the team sends the price of real branding production.",
            "params": {"quote": {"required": True, "validate": v_quote, "hint": ""}},
            "available": lambda state, client: False,
            "display": display_quote,
            "run": run_quote,
            "button": _txt("מאשר/ת את ההצעה", "Approve the quote", "Valider le devis", "الموافقة على العرض",
                           "Одобрить предложение"),
            "title": _txt("הצעת מחיר לייצור המיתוג", "A quote for producing your branding",
                          "Devis pour la production de votre identité", "عرض سعر لإنتاج الهوية",
                          "Цена на производство брендинга"),
            "what": _txt(
                "זו הצעת המחיר לייצור בפועל, לפי מה שביקשתם:\n{lines}\nסך הכול: ₪{total}. {note}\nשום דבר לא מיוצר לפני שאישרתם. אחרי האישור הצוות שולח בקשת תשלום, והייצור מתחיל אחרי התשלום.",
                "This is the quote for actual production, for what you asked:\n{lines}\nTotal: ₪{total}. {note}\nNothing is produced before you approve. After approval the team sends a payment request, and production starts once it is paid.",
                "Voici le devis pour la production réelle, selon votre demande :\n{lines}\nTotal : ₪{total}. {note}\nRien n'est produit avant votre validation. Ensuite l'équipe envoie une demande de paiement et la production démarre après paiement.",
                "هذا عرض السعر للإنتاج الفعلي حسب طلبكم:\n{lines}\nالإجمالي: ₪{total}. {note}\nلا يُنتج شيء قبل موافقتكم. بعد الموافقة يرسل الفريق طلب دفع ويبدأ الإنتاج بعد الدفع.",
                "Это цена на реальное производство по вашему запросу:\n{lines}\nИтого: ₪{total}. {note}\nНичего не изготавливается до вашего одобрения. После него команда отправит запрос на оплату, и производство начнётся после оплаты."),
            "done": _txt("אושר ✓ הצוות ישלח בקשת תשלום, והייצור מתחיל אחרי התשלום.",
                         "Approved ✓ The team will send a payment request, and production starts once it is paid.",
                         "Validé ✓ L'équipe envoie une demande de paiement ; la production démarre après paiement.",
                         "تمت الموافقة ✓ سيرسل الفريق طلب دفع ويبدأ الإنتاج بعد الدفع.",
                         "Одобрено ✓ Команда отправит запрос на оплату, производство начнётся после оплаты."),
            "refusals": {"no_open_quote": refusals["no_open_quote"]},
        },
    }


def send_quote(client_id: int, costs: dict, note: str = "") -> dict:
    """Staff send a production quote: stored, then shown to the client as ONE
    card in ליאור's chat. {"quote", "card"} or raises ValueError /
    branding.BrandingError."""
    from core import agent_actions
    current = branding.build_quote(costs, note)
    branding.store_quote(client_id, current)
    out = agent_actions.propose(client_id, "client_chat", "media",
                                {"name": "approve_production_quote", "params": {"quote": current["at"]}}, by_code=True)
    return {"quote": current, "card": out.get("card"), "error": out.get("error", "")}
