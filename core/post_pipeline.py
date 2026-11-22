"""A contract post from request to approved assets (2026-11-23,
core/post_brief, knowledge/social-craft.md §2, docs/POST-BRIEF.md).

  1. submit()   — the social agent's post (format + reason, audience, caption,
                  every slide's copy, one brief per asset). Context loaded in
                  full first (ContextMissing = a loud refusal). The media agent
                  reviews every brief (sourcing, craft §10); an objection is
                  round 1 = the social agent revises (nothing is queued), round
                  2 = escalated to the manager on the card with both positions.
                  Then the COPY waits in the Social tab (social_queue.add_post)
                  — nothing has been generated, no credit spent.
  2. start()    — the owner approved the copy there: production runs (background).
  3. produce()  — per asset, in the sourcing priority: text_slides → ONE design
                  item (core/slide_design: drawn with real fonts from one
                  template + one palette variant, never an image model; the
                  manager approves or critiques it); every other asset →
                  media_agent.generate_from_brief: the client's connected Drive
                  folders FIRST (core/drive_assets — the agent chooses a real
                  file itself, copied into the media folder, its folder + role
                  on the card); real_photo with nothing real → not made, never
                  generated; otherwise generated (prompt from visual_direction
                  only, assert_copy_free). Each file joins the queue as its own
                  item, linked to the post, for the publish approval as before.

Objection rounds live in app_settings `post_drafts:<client_id>` per set_id
(cleared with the client, guarded by deletion_guard).
"""
from datetime import datetime, timezone

from core import post_brief, social_queue

SERVICE_NAME = "post_pipeline"
DRAFTS_PREFIX = "post_drafts:"
MAX_DRAFT_SETS = 40


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


TEXT = {
    "context_missing": _five(
        "אי אפשר להכין את הפוסט כרגע: חסרים קבצי הידע שכל עבודת תוכן נשענת עליהם ({files}). שום דבר לא נוצר — הצוות שלנו קיבל התראה.",
        "The post can't be prepared right now: the knowledge files every content job relies on are missing ({files}). Nothing was made — our team has been alerted.",
        "Le post ne peut pas être préparé pour l'instant : les fichiers de connaissances indispensables manquent ({files}). Rien n'a été créé — notre équipe est prévenue.",
        "لا يمكن تجهيز المنشور الآن: ملفات المعرفة التي يعتمد عليها كل عمل محتوى ناقصة ({files}). لم يُنشأ شيء — تم تنبيه فريقنا.",
        "Пост сейчас нельзя подготовить: нет файлов знаний, на которые опирается любая работа с контентом ({files}). Ничего не создано — команда уведомлена."),
    "audience": _five(
        "למי הפוסט הזה מיועד — ללקוחות פרטיים (למשל מי שקונה מתנה) או לעסקים, קבוצות ואירועים? לכל קהל יש צבעים אחרים של המותג.",
        "Who is this post for — private customers (e.g. someone buying a gift) or businesses, teams and events? Each audience has its own brand colours.",
        "À qui s'adresse ce post — aux particuliers (par ex. quelqu'un qui offre un cadeau) ou aux entreprises, équipes et événements ? Chaque public a ses couleurs de marque.",
        "لمن هذا المنشور — للعملاء الأفراد (مثل من يشتري هدية) أم للشركات والفرق والفعاليات؟ لكل جمهور ألوان مختلفة للعلامة.",
        "Для кого этот пост — для частных клиентов (например, тех, кто покупает подарок) или для бизнеса, команд и мероприятий? У каждой аудитории свои цвета бренда."),
    "revise": _five(
        "לפני שמשהו נוצר, ליאור (המדיה) ביקש שינוי בחלק מהפוסט:\n{lines}\nאני מתקן ומגיש שוב.",
        "Before anything is made, Lior (media) asked for a change in part of the post:\n{lines}\nI'm fixing it and submitting again.",
        "Avant toute création, Lior (médias) demande un changement sur une partie du post :\n{lines}\nJe corrige et je soumets à nouveau.",
        "قبل إنشاء أي شيء، طلب ليئور (الوسائط) تغييرًا في جزء من المنشور:\n{lines}\nأصلح ذلك وأقدّمه من جديد.",
        "Прежде чем что-то создавать, Лиор (медиа) попросил изменить часть поста:\n{lines}\nИсправляю и отправляю снова."),
    "queued": _five(
        "הטקסט של הפוסט ({n} חלקים וכיתוב) מחכה לאישור שלכם בלשונית סושיאל. אפשר לערוך כל שקופית שם. שום דבר לא נוצר ושום קרדיט לא נוצל לפני שתאשרו.",
        "The post's copy ({n} parts and a caption) is waiting for your approval in the Social tab. You can edit every slide there. Nothing is generated and no credit is used before you approve.",
        "Le texte du post ({n} parties et une légende) attend votre accord dans l'onglet Social. Vous pouvez y modifier chaque diapositive. Rien n'est généré et aucun crédit n'est utilisé avant votre accord.",
        "نص المنشور ({n} أجزاء وتعليق) بانتظار موافقتكم في تبويب السوشيال. يمكن تعديل كل شريحة هناك. لا يُولَّد شيء ولا يُستخدم رصيد قبل موافقتكم.",
        "Текст поста ({n} частей и подпись) ждёт вашего одобрения во вкладке «Соцсети». Там можно отредактировать каждый слайд. Ничего не создаётся и кредиты не тратятся до вашего одобрения."),
    "escalated": _five(
        "בחלק אחד הסוכנים לא הסכימו גם אחרי תיקון — שתי העמדות מופיעות בכרטיס, וההחלטה שלכם.",
        "On one part the agents still disagree after a revision — both positions are on the card, and it's your call.",
        "Sur une partie, les agents ne sont toujours pas d'accord après révision — les deux positions sont sur la carte, à vous de décider.",
        "في جزء واحد ما زال الوكلاء مختلفين بعد التعديل — الموقفان في البطاقة، والقرار لكم.",
        "В одной части агенты не сошлись и после правки — обе позиции на карточке, решать вам."),
    "produced": _five(
        "הטקסט אושר והפקת הפוסט \"{title}\" הסתיימה: {made} קבצים מחכים לאישור הפרסום בלשונית סושיאל.{extra}",
        "The copy was approved and the post \"{title}\" is produced: {made} files wait for the publish approval in the Social tab.{extra}",
        "Le texte est validé et le post « {title} » est produit : {made} fichiers attendent l'accord de publication dans l'onglet Social.{extra}",
        "تمت الموافقة على النص وانتهى إنتاج المنشور \"{title}\": {made} ملفات بانتظار موافقة النشر في تبويب السوشيال.{extra}",
        "Текст одобрен, пост «{title}» готов: {made} файлов ждут одобрения публикации во вкладке «Соцсети».{extra}"),
    "not_made": _five(
        " {n} חלקים לא נוצרו במחשב: תמונות של העבודה עצמה צריכות להיות אמיתיות.",
        " {n} parts were not generated: photos of the work itself must be real.",
        " {n} parties n'ont pas été générées : les photos du travail doivent être réelles.",
        " {n} أجزاء لم تُولَّد: صور العمل نفسه يجب أن تكون حقيقية.",
        " {n} частей не сгенерировано: фото самой работы должны быть настоящими."),
    "design": _five(
        " {n} שקופיות הטקסט מחכות לכם כעיצוב לאישור בלשונית סושיאל (אפשר לכתוב הערה ואני מעצב מחדש).",
        " The {n} text slides wait for you as a design to approve in the Social tab (write a note and I'll redesign).",
        " Les {n} diapositives texte vous attendent comme design à valider dans l'onglet Social (une remarque, et je le refais).",
        " الشرائح النصية ({n}) بانتظاركم كتصميم للموافقة في تبويب السوشيال (اكتبوا ملاحظة وأعيد التصميم).",
        " {n} текстовых слайдов ждут вас как дизайн на одобрение во вкладке «Соцсети» (напишите замечание — переделаю)."),
    "design_failed": _five(
        " את שקופיות הטקסט לא הצלחתי לעצב בלי לשבור את כללי הקריאוּת — כדאי לקצר את הטקסט שלהן.",
        " I couldn't design the text slides without breaking the legibility rules — their copy should be shorter.",
        " Je n'ai pas pu concevoir les diapositives texte sans enfreindre les règles de lisibilité — leur texte doit être plus court.",
        " لم أتمكن من تصميم الشرائح النصية دون كسر قواعد الوضوح — يجب اختصار نصها.",
        " Не удалось оформить текстовые слайды, не нарушив правила читаемости, — их текст нужно сократить."),
}


def pick(table: dict, lang: str) -> str:
    return table.get(lang) or table.get("en") or next(iter(table.values()))


def _db():
    from agents.client_agent import _db as db
    return db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _alert(lines: list):
    try:
        from core.agent_base import agent_alert
        agent_alert(SERVICE_NAME, lines)
    except Exception:
        pass


# ─── Objection rounds (per client, per set_id) ───────────────────────────────

def _load_drafts(client_id: int) -> dict:
    rows = (_db().table("app_settings").select("value").eq("key", f"{DRAFTS_PREFIX}{int(client_id)}")
            .limit(1).execute().data or [])
    value = rows[0].get("value") if rows else None
    return value if isinstance(value, dict) else {}


def _save_drafts(client_id: int, drafts: dict):
    if len(drafts) > MAX_DRAFT_SETS:
        keep = sorted(drafts, key=lambda k: (drafts[k] or {}).get("updated_at", ""))[-MAX_DRAFT_SETS:]
        drafts = {k: drafts[k] for k in keep}
    _db().table("app_settings").upsert({"key": f"{DRAFTS_PREFIX}{int(client_id)}", "value": drafts,
                                        "updated_at": _now()}, on_conflict="key").execute()


# ─── 1. Submit: context, format, audience, review, the copy to the queue ─────

def submit(client_id: int, raw_post, request_text: str = "", lang: str = "he", persona: str = "media") -> dict:
    """One of: {"clarify": text} (ONE question), {"refuse": text} (context
    missing / the media agent's round-1 objection), or {"queued": item,
    "line": text}. Never generates anything."""
    try:
        context = post_brief.load_context(client_id)
    except post_brief.ContextMissing as e:
        _alert([f"client {client_id}: {e} — no post can be prepared until both files exist"])
        return {"refuse": pick(TEXT["context_missing"], lang).replace("{files}", ", ".join(e.missing)),
                "code": "context_missing"}
    try:
        post = post_brief.validate_post(raw_post, client_id, request_text, lang)
    except post_brief.FormatQuestion as q:
        return {"clarify": pick(q.question, lang), "code": "format_question"}
    try:
        palette = post_brief.resolve_palette(context["brand_facts"], post["audience"])
    except post_brief.AudienceUnresolved:
        return {"clarify": pick(TEXT["audience"], lang), "code": "audience_question"}

    from agents import media_agent
    drafts = _load_drafts(client_id)
    draft = drafts.setdefault(post["set_id"], {"objections": []})
    revise, escalations, flags, sources = [], {}, {}, {}
    for entry in post["assets"]:
        brief = post_brief.asset(entry)
        review = media_agent.review_brief(brief)
        flags[brief.asset_id] = list(review["flags"])
        if review.get("real_asset"):
            sources[brief.asset_id] = review["real_asset"]      # the card shows its folder + role
        if brief.asset_type == "text_slide":
            flags[brief.asset_id].append("text_slide_pending")
        elif brief.asset_type == "real_photo" and not review.get("real_asset"):
            flags[brief.asset_id].append("needs_real_asset")
        if not review["objection"]:
            continue
        if post_brief.escalated(draft["objections"], brief.asset_id):
            flags[brief.asset_id].append("escalated")
            escalations[brief.asset_id] = _positions(draft["objections"], brief.asset_id)
            continue
        outcome = post_brief.record_objection(draft["objections"], brief.asset_id, "media",
                                              review["objection"], entry.get("response", ""))
        if outcome["action"] == "revise":
            revise.append(f"• {brief.asset_id}: {outcome['text']}")
        else:
            flags[brief.asset_id].append("escalated")
            escalations[brief.asset_id] = outcome["positions"]
    draft["updated_at"] = _now()
    _save_drafts(client_id, drafts)
    if revise:
        return {"refuse": pick(TEXT["revise"], lang).replace("{lines}", "\n".join(revise)), "code": "objection"}

    warning = post_brief.lead_time_warning(context["brand_facts"], post.get("occasion_date", ""),
                                           post.get("publish_date", ""))
    warnings = [{**warning, "occasion_date": post["occasion_date"]}] if warning else []
    item = social_queue.add_post(client_id, post, palette=palette, warnings=warnings,
                                 escalations=escalations, flags=flags, sources=sources,
                                 producer=persona if persona in social_queue.PRODUCERS else "media")
    line = pick(TEXT["queued"], lang).replace("{n}", str(len(post["assets"])))
    if escalations:
        line += " " + pick(TEXT["escalated"], lang)
    for w in social_queue.warning_lines(warnings, lang):
        line += "\n" + w
    return {"queued": item, "line": line}


def _positions(history: list, asset_id: str) -> dict:
    rows = [h for h in history if h.get("asset_id") == asset_id]
    last = rows[-1] if rows else {}
    return {"media": last.get("text", ""), "counterpart": last.get("response") or "kept the asset unchanged"}


# ─── 2-3. The owner approved the copy: production ────────────────────────────

def start(client_id: int, items: list, background_tasks=None) -> int:
    """After social_queue.approve: every approved POST item starts production
    (in the background when possible). Returns how many were started."""
    started = 0
    for item in items or []:
        if item.get("kind") == social_queue.DESIGN_KIND:
            # An approved DESIGN: saved as a template; a post's slides rendered
            from core import slide_design
            if background_tasks is not None:
                background_tasks.add_task(slide_design.approved, client_id, item["id"])
            else:
                slide_design.approved(client_id, item["id"])
            started += 1
            continue
        if item.get("kind") != social_queue.POST_KIND:
            continue
        social_queue.set_production(client_id, item["id"], {"state": "running", "started_at": _now()})
        if background_tasks is not None:
            background_tasks.add_task(produce, client_id, item["id"])
        else:
            produce(client_id, item["id"])
        started += 1
    return started


def produce(client_id: int, item_id: str) -> dict:
    """Every asset of an approved post, in order, within its credit budget.
    Never raises; the result is recorded on the post item."""
    item = social_queue.get(client_id, item_id)
    if not item or item.get("kind") != social_queue.POST_KIND or item.get("status") != "approved":
        return {"state": "failed", "reason": "not_an_approved_post"}
    body = item.get("post") or {}
    slides = body.get("slides") or []
    palette = body.get("palette") or {}
    budget = sum(int(s.get("budget_credits") or 0) for s in slides)
    spent, made, not_made, failed, results = 0.0, 0, 0, 0, []
    text_slides = []
    from agents import media_agent
    for n, slide in enumerate(slides, 1):
        if slide["asset_type"] == "text_slide":
            # Drawn by the slide renderer as ONE set (one template, one palette
            # variant) — never an image model. The design waits for approval.
            text_slides.append({**slide, "n": n, "total": len(slides)})
            continue
        brief = post_brief.AssetBrief(
            asset_id=slide["asset_id"], post_format=body["post_format"], objective=body["objective"],
            client_id=int(client_id), role=slide["role"],
            # The APPROVED copy, carried for context only — generate_from_brief
            # never puts it in a prompt (assert_copy_free checks it)
            copy_headline=slide.get("copy_headline", ""), copy_body=slide.get("copy_body", ""),
            asset_type=slide["asset_type"], visual_direction=slide.get("visual_direction", ""),
            must_include=list(slide.get("must_include") or []), must_avoid=list(slide.get("must_avoid") or []),
            aspect_ratio=slide.get("aspect_ratio") or "4:5", set_id=body["set_id"],
            budget_credits=int(slide.get("budget_credits") or 0))
        if brief.asset_type.startswith("generated") and budget and spent >= budget:
            results.append({"asset_id": brief.asset_id, "status": "over_budget"})
            not_made += 1
            continue
        try:
            out = media_agent.generate_from_brief(client_id, brief, palette, platform=(item["channels"] or ["instagram"])[0])
        except Exception as e:
            out = {"success": False, "status": "failed", "errors": [f"{type(e).__name__}: {str(e)[:200]}"]}
        if not out.get("success"):
            status = out.get("status") or "failed"
            results.append({"asset_id": brief.asset_id, "status": status})
            if status in (post_brief.RENDERER_NOT_IMPLEMENTED, "needs_real_asset"):
                not_made += 1
            else:
                failed += 1
                if status in ("no_account", "context_missing"):
                    break               # the next assets would fail the same way
            continue
        spent += float(out.get("credits") or 0)
        # A real asset from the client's folder is not a concept image
        flags = ["concept"] if "concept" in (slide.get("flags") or []) and not out.get("real_asset") else []
        added = social_queue.add_safely(
            client_id, out["file_id"], out.get("kind") or "image",
            [c for c in item["channels"] if (out.get("kind") or "image") in social_queue.CHANNEL_KINDS.get(c, ())]
            or ["instagram"],
            caption=item.get("caption", ""), planned_date=item.get("planned_date", ""), producer="media",
            source="post", title=f"{item.get('title') or body['set_id']} · {n}/{len(slides)}", flags=flags,
            post_ref={"post_id": item["id"], "set_id": body["set_id"], "asset_id": brief.asset_id,
                      "n": n, "of": len(slides)},
            real_asset=out.get("real_asset") or None)
        results.append({"asset_id": brief.asset_id, "status": "made", "queue_id": added.get("id", ""),
                        "link": out.get("link", ""), "source": "real_asset" if out.get("real_asset") else "generated"})
        made += 1
    design = {}
    if text_slides:
        from core import slide_design
        opened = slide_design.open_for_post(client_id, item, text_slides, _lang(client_id))
        design = {"item_id": (opened.get("item") or {}).get("id", ""), "slides": len(text_slides),
                  "ok": bool(opened.get("item")) and not opened.get("problems")}
        results += [{"asset_id": s["asset_id"], "status": "design_waiting" if design["item_id"] else "failed"}
                    for s in text_slides]
        if not design["item_id"]:
            failed += len(text_slides)
    state = ("done" if (made or design.get("item_id")) and not failed else "partial" if made or design.get("item_id")
             else "failed" if failed else "done")
    production = {"state": state, "made": made, "not_made": not_made, "failed": failed,
                  "credits": round(spent, 2), "assets": results, "finished_at": _now(), "design": design}
    social_queue.set_production(client_id, item_id, production)
    _tell(client_id, item, production)
    if failed:
        _alert([f"client {client_id}: post {body.get('set_id')} — {failed} asset(s) failed in production"])
    return production


def _lang(client_id: int) -> str:
    try:
        from core import agent_actions
        return agent_actions._lang(client_id)
    except Exception:
        return "he"


def _tell(client_id: int, item: dict, production: dict):
    """One line in the producer's chat. Never raises."""
    try:
        from core import agent_actions
        from agents.client_agent import log_communication
        lang = agent_actions._lang(client_id)
        extra = pick(TEXT["not_made"], lang).replace("{n}", str(production["not_made"])) if production["not_made"] else ""
        design = production.get("design") or {}
        if design.get("item_id"):
            extra += pick(TEXT["design" if design.get("ok") else "design_failed"], lang).replace("{n}", str(design["slides"]))
        line = (pick(TEXT["produced"], lang).replace("{title}", item.get("title") or "")
                .replace("{made}", str(production["made"])).replace("{extra}", extra))
        log_communication(client_id, "outbound", f"dashboard_chat:{item.get('producer') or 'media'}", line)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: production line not posted ({type(e).__name__})")
