"""Text-slide design: the agent designs, the manager critiques, the template
remembers (2026-11-23, docs/SLIDE-RENDERER.md).

  open_for_post()  the post's copy was approved: its text_slide assets become
                   ONE design item in the Social tab (kind "design") — the
                   whole set from ONE spec and ONE palette variant. The spec
                   starts from the client's latest template for that variant
                   (else the plain starter) and only goes to the agent when a
                   guard fails.
  request()        "design me a template" from the chat (design_slides card):
                   the agent designs from the manager's direction.
  critique()       the manager's free-text note on the card → stored against
                   the template (the client's taste) → revise() in the
                   background: the media agent rewrites the SPEC, it is
                   rendered and checked (free, milliseconds, up to
                   MAX_DESIGN_ATTEMPTS), and the item shows the new revision.
  approved()       the manager approved the design: saved as a named,
                   versioned template; for a post, every slide is rendered,
                   saved to the client's Drive and added to the Social tab
                   as approved (those exact pixels were just approved).
  preview_png()    one slide of a design item, rendered on request for the
                   card — nothing is stored to show it.

No credits are spent anywhere here and no image model is ever called: the
text is drawn by core/slide_renderer. Background photos and logos come only
from the client's OWN media folder (a fresh parents walk per file).
"""
import copy
import re
from datetime import datetime, timezone

from core import post_brief, slide_renderer, slide_spec, slide_templates, social_queue

SERVICE_NAME = "slide_design"
MAX_DESIGN_ATTEMPTS = 3
IMAGE_MAX_BYTES = 15 * 1024 * 1024
STATES = ("ready", "revising", "failed")
KEEP_SPECS = 6


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


pick = social_queue.pick

TEXT = {
    "queued": _five(
        "העיצוב של {n} שקופיות הטקסט מחכה לכם בלשונית סושיאל — התמונות עצמן והמפרט שלהן. אפשר לאשר, או לכתוב הערה ואני מעצב מחדש. בלי קרדיטים.",
        "The design of the {n} text slides is waiting in the Social tab — the images and their spec. Approve it, or write a note and I'll redesign. No credits involved.",
        "Le design des {n} diapositives texte vous attend dans l'onglet Social — les images et leur spécification. Validez-le, ou écrivez une remarque et je le refais. Aucun crédit utilisé.",
        "تصميم {n} شرائح نصية بانتظاركم في تبويب السوشيال — الصور ومواصفاتها. وافقوا عليه، أو اكتبوا ملاحظة وأعيد التصميم. بلا أي رصيد.",
        "Дизайн {n} текстовых слайдов ждёт во вкладке «Соцсети» — сами изображения и их спецификация. Одобрите или напишите замечание, и я переделаю. Без кредитов."),
    "revised": _five(
        "עדכנתי את העיצוב לפי ההערה שלכם — הגרסה החדשה ({rev}) בלשונית סושיאל.",
        "I updated the design per your note — the new version ({rev}) is in the Social tab.",
        "J'ai mis à jour le design selon votre remarque — la nouvelle version ({rev}) est dans l'onglet Social.",
        "حدّثت التصميم حسب ملاحظتكم — النسخة الجديدة ({rev}) في تبويب السوشيال.",
        "Я обновил дизайн по вашему замечанию — новая версия ({rev}) во вкладке «Соцсети»."),
    "revise_failed": _five(
        "לא הצלחתי ליישם את ההערה בלי לשבור את כללי הקריאוּת ({problem}). הגרסה הקודמת נשארה בלשונית — אפשר לנסח את ההערה אחרת.",
        "I couldn't apply the note without breaking the legibility rules ({problem}). The previous version stays in the tab — try wording the note differently.",
        "Je n'ai pas pu appliquer la remarque sans enfreindre les règles de lisibilité ({problem}). La version précédente reste dans l'onglet — reformulez la remarque.",
        "لم أتمكن من تطبيق الملاحظة دون كسر قواعد الوضوح ({problem}). بقيت النسخة السابقة في التبويب — جرّبوا صياغة أخرى للملاحظة.",
        "Не удалось учесть замечание, не нарушив правила читаемости ({problem}). Прежняя версия осталась во вкладке — попробуйте сформулировать иначе."),
    "approved": _five(
        "העיצוב אושר ונשמר כתבנית \"{name}\" (גרסה {version}).{extra}",
        "The design is approved and saved as the template \"{name}\" (version {version}).{extra}",
        "Le design est validé et enregistré comme modèle « {name} » (version {version}).{extra}",
        "تمت الموافقة على التصميم وحُفظ كقالب \"{name}\" (النسخة {version}).{extra}",
        "Дизайн одобрен и сохранён как шаблон «{name}» (версия {version}).{extra}"),
    "files": _five(
        " {n} שקופיות נשמרו בתיקייה ומאושרות לפרסום בלשונית סושיאל.",
        " {n} slides were saved to your folder and are approved for publishing in the Social tab.",
        " {n} diapositives sont enregistrées dans votre dossier et validées pour publication dans l'onglet Social.",
        " حُفظت {n} شرائح في مجلدكم وهي معتمدة للنشر في تبويب السوشيال.",
        " {n} слайдов сохранены в папке и одобрены к публикации во вкладке «Соцсети»."),
    "files_failed": _five(
        " את השקופיות עצמן לא הצלחתי לשמור בתיקייה — הצוות קיבל התראה.",
        " The slides themselves could not be saved to your folder — our team has been alerted.",
        " Les diapositives n'ont pas pu être enregistrées dans votre dossier — notre équipe est prévenue.",
        " تعذّر حفظ الشرائح نفسها في مجلدكم — تم تنبيه فريقنا.",
        " Сами слайды не удалось сохранить в папку — команда уведомлена."),
    "cant_design": _five(
        "לא הצלחתי לעצב את שקופיות הטקסט בלי לשבור את כללי הקריאוּת ({problem}). כדאי לקצר את הטקסט של השקופית או לכתוב לי מה לשנות בעיצוב.",
        "I couldn't design the text slides without breaking the legibility rules ({problem}). Shorten that slide's copy, or tell me what to change in the design.",
        "Je n'ai pas pu concevoir les diapositives texte sans enfreindre les règles de lisibilité ({problem}). Raccourcissez le texte ou dites-moi quoi changer.",
        "لم أتمكن من تصميم الشرائح النصية دون كسر قواعد الوضوح ({problem}). اختصروا نص الشريحة أو أخبروني بما أغيّر في التصميم.",
        "Не удалось оформить текстовые слайды, не нарушив правила читаемости ({problem}). Сократите текст слайда или скажите, что изменить в дизайне."),
    "stage": _five(
        "שלב העיצוב: אישור שומר את העיצוב כתבנית של העסק; הערה שולחת אותו לעיצוב מחדש. בלי קרדיטים.",
        "Design stage: approving saves it as your business's template; a note sends it back for a redesign. No credits.",
        "Étape design : valider l'enregistre comme modèle de votre entreprise ; une remarque le renvoie en refonte. Aucun crédit.",
        "مرحلة التصميم: الموافقة تحفظه كقالب لعملكم؛ والملاحظة تعيده لإعادة التصميم. بلا رصيد.",
        "Этап дизайна: одобрение сохраняет его как шаблон вашего бизнеса; замечание отправляет на переделку. Без кредитов."),
}

STATE_TEXT = {
    "ready": _five("מוכן לבדיקה", "Ready for review", "Prêt à vérifier", "جاهز للمراجعة", "Готов к проверке"),
    "revising": _five("מעצב מחדש לפי ההערה…", "Redesigning per your note…", "Refonte selon votre remarque…",
                      "إعادة التصميم حسب ملاحظتكم…", "Переделываю по замечанию…"),
    "failed": _five("העיצוב לא עבר את כללי הקריאוּת", "The design failed the legibility rules",
                    "Le design ne respecte pas les règles de lisibilité", "التصميم لم يجتز قواعد الوضوح",
                    "Дизайн не прошёл правила читаемости"),
}

PROBLEM_TEXT = {
    "overflow": _five("טקסט ארוך מדי למקום שלו", "text too long for its space", "texte trop long pour son espace",
                      "نص أطول من مساحته", "текст длиннее отведённого места"),
    "headline_words": _five("כותרת ארוכה מדי", "headline too long", "titre trop long", "عنوان طويل جدًا",
                            "слишком длинный заголовок"),
    "contrast": _five("הטקסט לא בולט מספיק על הרקע", "text doesn't stand out from the background",
                      "texte pas assez contrasté", "النص لا يبرز على الخلفية", "текст плохо виден на фоне"),
    "overlap": _five("טקסטים עולים זה על זה", "texts overlap", "textes superposés", "نصوص متداخلة",
                     "тексты перекрываются"),
    "asset": _five("תמונה או לוגו שלא נמצאו בתיקייה שלכם", "a photo or logo not found in your folder",
                   "photo ou logo introuvable dans votre dossier", "صورة أو شعار غير موجود في مجلدكم",
                   "фото или логотип не найдены в вашей папке"),
    "other": _five("מפרט עיצוב לא תקין", "an invalid design spec", "une spécification invalide",
                   "مواصفات تصميم غير صالحة", "некорректная спецификация"),
}

SPEC_LABELS = {
    "canvas": _five("גודל", "Size", "Format", "المقاس", "Размер"),
    "background": _five("רקע", "Background", "Fond", "الخلفية", "Фон"),
    "kicker": _five("כותרת עליונה", "Kicker", "Surtitre", "عنوان علوي", "Надзаголовок"),
    "headline": _five("כותרת", "Headline", "Titre", "العنوان", "Заголовок"),
    "body": _five("גוף", "Body", "Corps", "النص", "Текст"),
    "cta": _five("קריאה לפעולה", "Call to action", "Appel à l'action", "دعوة لإجراء", "Призыв"),
    "accent": _five("הדגשה", "Accent", "Accent", "إبراز", "Акцент"),
    "logo": _five("לוגו", "Logo", "Logo", "الشعار", "Логотип"),
    "counter": _five("מונה שקופיות", "Slide counter", "Compteur", "عداد الشرائح", "Счётчик"),
    "margins": _five("שוליים", "Margins", "Marges", "الهوامش", "Поля"),
    "overrides": _five("שקופיות מיוחדות", "Per-role slides", "Diapositives par rôle", "شرائح حسب الدور",
                       "Слайды по ролям"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _alert(lines: list):
    try:
        from core.agent_base import agent_alert
        agent_alert(SERVICE_NAME, lines)
    except Exception:
        pass


def _problem_lines(problems: list, lang: str) -> list:
    from core import copy_budget
    return copy_budget.problem_lines([p for p in problems or [] if p.get("slide")], lang)


def problem_text(problems: list, lang: str) -> str:
    codes = list(dict.fromkeys((p.get("code") if p.get("code") in PROBLEM_TEXT else "other") for p in problems))
    return ", ".join(pick(PROBLEM_TEXT[c], lang) for c in codes[:3]) or pick(PROBLEM_TEXT["other"], lang)


# ─── Assets: the client's OWN files only ─────────────────────────────────────

def _fetch_image(client_id: int, asset_id: str) -> bytes:
    from core import drive_storage
    return drive_storage.scoped(_fetch_image_scoped)(client_id, asset_id)


def _fetch_image_scoped(client_id: int, asset_id: str) -> bytes:
    from core import drive_mirror, drive_service as drive
    if drive_mirror.path_in_client_tree(client_id, asset_id) is None:
        raise PermissionError("not in the client's media folder")
    meta = drive.get_meta(asset_id, "mimeType,size")
    if not str(meta.get("mimeType") or "").startswith("image/"):
        raise ValueError("not an image")
    if int(meta.get("size") or 0) > IMAGE_MAX_BYTES:
        raise ValueError("image larger than 15 MB")
    return drive.download_file(asset_id)


def loader_for(client_id: int):
    """An image loader for one render pass (each file fetched once)."""
    cache = {}

    def load(asset_id: str) -> bytes:
        if asset_id not in cache:
            if str(asset_id).startswith("pose:"):
                # An agent's cut-out from the pose library (our Shared Drive) —
                # the one file outside the client's folder a slide may use
                from core import agent_characters
                cache[asset_id] = agent_characters.pose_bytes(str(asset_id)[5:])
            else:
                cache[asset_id] = _fetch_image(client_id, asset_id)
        return cache[asset_id]
    return load


# ─── Spec, palette, checks ───────────────────────────────────────────────────

def default_name(palette: dict) -> str:
    return slide_templates.clean_name(f"carousel {slide_spec.variant_for(palette)}")


def base_spec(client_id: int, palette: dict, aspect: str = "4:5", template_name: str = "") -> tuple:
    """(spec, template_name, based_on): the named template, else the latest
    template in this palette variant, else the plain starter."""
    label = slide_spec.variant_for(palette)
    tpl = slide_templates.get(client_id, template_name) if template_name else None
    if tpl and tpl["palette_variant"] != label:
        tpl = None                               # never mix variants: that template is for another audience
    tpl = tpl or slide_templates.for_variant(client_id, label)
    width, height = slide_spec.CANVASES.get(aspect, slide_spec.DEFAULT_CANVAS)
    if tpl:
        spec = tpl["spec"]
        spec["canvas"] = {"width": width, "height": height}
        return spec, tpl["name"], {"template": tpl["name"], "version": tpl["version"]}
    segment = (palette or {}).get("segment") or "b2c"
    spec = slide_spec.starter(segment, label, aspect)
    return spec, template_name or default_name(palette), {"starter": segment}


def check(client_id: int, spec: dict, palette: dict, slides: list, loader=None, warnings: list = None) -> list:
    """Every guard on every slide of the set, nothing encoded. [] = renders.
    `warnings` (a list) gets the character warnings (overflow / text)."""
    label = slide_spec.variant_for(palette)
    if spec.get("palette_variant") != label:
        return [{"code": "palette", "detail": f"spec palette_variant {spec.get('palette_variant')!r} is not this "
                                              f"set's variant {label!r} — a set never mixes variants"}]
    try:
        roles = slide_spec.palette_roles(client_id, label)
        slide_renderer.render_set(spec, roles, slides, loader or loader_for(client_id), encode=False,
                                  warnings=warnings)
    except slide_spec.SpecInvalid as e:
        return [{"code": "bad_spec", "detail": d} for d in e.errors]
    except slide_renderer.RenderError as e:
        return e.problems
    return []


def design(client_id: int, spec: dict, palette: dict, slides: list, critique: str = "", direction: str = "",
           lang: str = "he", template_name: str = "") -> dict:
    """{"spec", "rationale", "attempts", "problems"}: the spec as-is when it
    renders and nobody asked for a change; otherwise the media agent rewrites
    it (free: Claude writes JSON, the renderer checks it) until it passes, up
    to MAX_DESIGN_ATTEMPTS. problems != [] = it never passed (spec = the last
    one that did, or the input)."""
    loader = loader_for(client_id)
    spec = placement_only(spec)
    problems = check(client_id, spec, palette, slides, loader)
    if not problems and not critique and not direction:
        return {"spec": spec, "rationale": "", "attempts": 0, "problems": []}
    from agents import media_agent
    return media_agent.design_spec(
        client_id, spec=spec, palette=palette, roles=slide_spec.palette_roles(client_id, slide_spec.variant_for(palette)),
        slides=slides, problems=problems, critique=critique, direction=direction, lang=lang,
        taste=slide_templates.critiques(client_id, limit=15),
        check=lambda s: check(client_id, placement_only(s), palette, slides, loader), attempts=MAX_DESIGN_ATTEMPTS)


def placement_only(spec: dict) -> dict:
    """A spec's `character` keeps the PLACEMENT only: which agent and which
    pose are the system's, per slide — never the designer's (or a template's
    from another post)."""
    if not isinstance(spec, dict):
        return spec
    parts = [spec] + [v for v in (spec.get("role_overrides") or {}).values() if isinstance(v, dict)]
    for part in parts:
        if isinstance(part.get("character"), dict):
            for k in ("agent", "pose_id"):
                part["character"].pop(k, None)
    return spec


def _slides_of(entries: list) -> list:
    out = []
    for s in entries:
        row = {"asset_id": s.get("asset_id", ""), "role": s.get("role", ""), "copy_headline": s.get("copy_headline", ""),
               "copy_body": s.get("copy_body", ""), "n": s.get("n"), "total": s.get("total")}
        if s.get("character"):
            row["character"] = dict(s["character"])         # {"agent", "pose_id"} — the system's, per slide
        if s.get("colors"):
            row["colors"] = dict(s["colors"])               # the manager's explicit colours (post `colors`)
        out.append(row)
    return out


# ─── Agents as their own characters: COMPOSITE (core/agent_characters) ───────

POSE_BY_ROLE = {"hook": "waving hello with a big welcoming smile, turned slightly toward the viewer",
                "point": "pointing with one hand to the side, explaining, friendly and confident",
                "proof": "giving a thumbs up, proud and relaxed",
                "cta": "gesturing invitingly with an open hand, encouraging"}


def attach_characters(entries: list, lang: str = "he", stopped=None) -> tuple:
    """Each composite slide with a character gets a pose: a stored approved
    cut-out FIRST (free), else a new one — every new one of the post made AT
    ONCE (agent_characters.poses_for), not one after another. Returns
    (entries, failures) — a slide whose pose could not be made renders
    without it (alerted), and EVERY failure carries the manager-facing line
    (agent_characters.failure_text)."""
    from core import agent_characters
    wanted = []
    for i, s in enumerate(entries):
        names = list(s.get("characters") or [])
        if names and (s.get("character_mode") or "composite") == "composite":
            pose = (s.get("visual_direction") or "").strip() or POSE_BY_ROLE.get(s.get("role"), POSE_BY_ROLE["point"])
            wanted.append((i, names[0], pose))
    results = agent_characters.poses_for([(agent, pose) for _, agent, pose in wanted], stopped=stopped) if wanted else []
    by_index = {i: (agent, got) for (i, agent, _), got in zip(wanted, results)}
    failures, out = [], []
    for i, s in enumerate(entries):
        if i not in by_index:
            out.append(s)
            continue
        agent, got = by_index[i]
        if isinstance(got, agent_characters.StopRequested):
            out.append(s)               # the run was stopped: not a failure, nothing was submitted
            continue
        if isinstance(got, BaseException):
            failures.append({"asset_id": s.get("asset_id", ""), "agent": agent,
                             "reason": getattr(got, "reason", "") or f"{type(got).__name__}: {str(got)[:150]}",
                             # the manager-facing line, whatever failed (never only a log line)
                             "message": agent_characters.failure_text(agent, got, lang)})
            out.append(s)
        else:
            out.append({**s, "character": {"agent": agent, "pose_id": got["pose_id"]}})
    return out, failures


def character_flags(client_id: int, spec: dict, palette: dict, slides: list, loader=None) -> list:
    """The design card's notices: AI-character labels + the renderer's
    character warnings (shown, never blocking)."""
    from core import agent_characters
    if not any(s.get("character") for s in slides):
        return []
    warnings = []
    check(client_id, spec, palette, slides, loader, warnings=warnings)
    codes = list(dict.fromkeys(w["code"] for w in warnings))
    return agent_characters.approval_flags(client_id, True) + codes


def _design_record(spec, palette, slides, template_name, based_on, result, set_id="", post_id="") -> dict:
    ok = not result["problems"]
    return {"template_name": template_name, "based_on": based_on,
            "palette": {"label": slide_spec.variant_for(palette), "segment": (palette or {}).get("segment", "")},
            "spec": result["spec"], "rev": 1, "slides": slides, "set_id": set_id, "post_id": post_id,
            "state": "ready" if ok else "failed", "problems": result["problems"][:10],
            "rationale": result.get("rationale", ""), "critiques": [],
            "revisions": [{"rev": 1, "at": _now(), "by": "media", "rationale": result.get("rationale", ""),
                           "spec": result["spec"]}]}


# ─── Entry points ────────────────────────────────────────────────────────────

def open_for_post(client_id: int, post_item: dict, text_slides: list, lang: str = "he") -> dict:
    """The approved post's text slides → ONE design item. Never raises."""
    body = post_item.get("post") or {}
    palette = body.get("palette") or {}
    try:
        entries, missing = attach_characters(text_slides, lang)
        if missing:
            _alert([f"client {client_id}: post {post_item.get('id')} — agent character not drawn on "
                    f"{', '.join(m['asset_id'] + ' (' + m['agent'] + ': ' + m['reason'] + ')' for m in missing)}"])
        slides = _slides_of(entries)
        spec, name, based = base_spec(client_id, palette, text_slides[0].get("aspect_ratio") or "4:5")
        result = design(client_id, spec, palette, slides, lang=lang, template_name=name)
        record = _design_record(spec, palette, slides, name, based, result, body.get("set_id", ""), post_item["id"])
        flags = character_flags(client_id, result["spec"], palette, slides) if not result["problems"] else []
        flags += ["character_unavailable"] if missing else []
        item = social_queue.add_design(client_id, record, title=post_item.get("title") or body.get("set_id", ""),
                                       channels=post_item.get("channels"), caption=post_item.get("caption", ""),
                                       planned_date=post_item.get("planned_date", ""), flags=flags)
        return {"item": item, "problems": result["problems"],
                "character_errors": list(dict.fromkeys(m["message"] for m in missing if m.get("message")))}
    except Exception as e:
        _alert([f"client {client_id}: text-slide design for post {post_item.get('id')} failed "
                f"({type(e).__name__}: {str(e)[:200]})"])
        return {"item": {}, "problems": [{"code": "other", "detail": f"{type(e).__name__}"}], "error": str(e)[:200]}


def request(client_id: int, slides: list, audience: str, direction: str = "", template_name: str = "",
            aspect: str = "4:5", lang: str = "he", title: str = "") -> dict:
    """The chat's design_slides: {"queued", "line"} | {"clarify"} | {"refuse"}."""
    from core import post_pipeline
    try:
        context = post_brief.load_context(client_id)
    except post_brief.ContextMissing as e:
        _alert([f"client {client_id}: {e} — no design can be made until both files exist"])
        return {"refuse": pick(post_pipeline.TEXT["context_missing"], lang).replace("{files}", ", ".join(e.missing)),
                "code": "context_missing"}
    try:
        palette = post_brief.resolve_palette(context["brand_facts"], audience)
    except post_brief.AudienceUnresolved:
        return {"clarify": pick(post_pipeline.TEXT["audience"], lang), "code": "audience_question"}
    total = len(slides)
    entries = [{**s, "n": i, "total": total} for i, s in enumerate(slides, 1)]
    spec, name, based = base_spec(client_id, palette, aspect, template_name)
    result = design(client_id, spec, palette, _slides_of(entries), direction=direction, lang=lang, template_name=name)
    if result["problems"]:
        return {"refuse": pick(TEXT["cant_design"], lang).replace("{problem}", problem_text(result["problems"], lang)),
                "code": "cant_design"}
    record = _design_record(spec, palette, _slides_of(entries), template_name or name, based, result)
    item = social_queue.add_design(client_id, record, title=title or record["template_name"], channels=["instagram"])
    return {"queued": item, "line": pick(TEXT["queued"], lang).replace("{n}", str(total))}


def critique(client_id: int, item_id: str, text: str, by: str = "client", background_tasks=None) -> dict:
    """The manager's note → kept against the template → the agent revises."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) < 2:
        raise social_queue.QueueError("critique_required")
    item = social_queue.get(client_id, item_id)
    if not item or item.get("kind") != social_queue.DESIGN_KIND:
        raise social_queue.QueueError("not_a_design")
    if item["status"] != "waiting":
        raise social_queue.QueueError("not_editable")
    d = item["design"]
    if d.get("state") == "revising":
        raise social_queue.QueueError("busy")
    slide_templates.add_critique(client_id, d["template_name"], text, version=(d.get("based_on") or {}).get("version"),
                                 item_id=item_id, by=by)
    item = social_queue.update_design(client_id, item_id, {
        "state": "revising", "critiques": (d.get("critiques") or [])[-19:] + [{"at": _now(), "text": text[:600],
                                                                              "rev": d.get("rev", 1)}]})
    if background_tasks is not None:
        background_tasks.add_task(revise, client_id, item_id, text)
    else:
        revise(client_id, item_id, text)
    return item


def revise(client_id: int, item_id: str, text: str) -> dict:
    """One revision from a critique. Never raises; the item says how it went."""
    lang = _lang(client_id)
    item = social_queue.get(client_id, item_id) or {}
    d = item.get("design") or {}
    try:
        result = design(client_id, d["spec"], d["palette"], d["slides"], critique=text, lang=lang,
                        template_name=d["template_name"])
    except Exception as e:
        result = {"spec": d.get("spec"), "problems": [{"code": "other", "detail": f"{type(e).__name__}: {str(e)[:150]}"}]}
    if result["problems"]:
        social_queue.update_design(client_id, item_id, {"state": "ready", "last_failure": result["problems"][:10]})
        _tell(client_id, item, pick(TEXT["revise_failed"], lang).replace("{problem}", problem_text(result["problems"], lang)))
        return {"ok": False, "problems": result["problems"]}
    rev = int(d.get("rev") or 1) + 1
    revisions = (d.get("revisions") or [])[-(KEEP_SPECS - 1):] + [
        {"rev": rev, "at": _now(), "by": "media", "rationale": result.get("rationale", ""), "spec": result["spec"]}]
    social_queue.update_design(client_id, item_id, {"spec": result["spec"], "rev": rev, "state": "ready",
                                                    "problems": [], "last_failure": [], "revisions": revisions,
                                                    "rationale": result.get("rationale", "")})
    _tell(client_id, item, pick(TEXT["revised"], lang).replace("{rev}", str(rev)))
    return {"ok": True, "rev": rev}


def approved(client_id: int, item_id: str) -> dict:
    """After the manager approved a design item: the template is saved, and a
    post's slides are rendered to the client's Drive as approved items."""
    lang = _lang(client_id)
    item = social_queue.get(client_id, item_id) or {}
    d = item.get("design") or {}
    if item.get("kind") != social_queue.DESIGN_KIND or item.get("status") != "approved":
        return {"ok": False, "reason": "not_an_approved_design"}
    saved = slide_templates.save(client_id, d["template_name"], d["spec"], by="client",
                                 note=f"approved in the Social tab ({item_id}, revision {d.get('rev', 1)})")
    production = {"state": "done", "made": 0, "template": {"name": saved["name"], "version": saved["version"]}}
    extra = ""
    if d.get("post_id"):
        try:
            made = _finalize(client_id, item, saved)
            production.update(made=len(made), items=made)
            extra = pick(TEXT["files"], lang).replace("{n}", str(len(made)))
        except Exception as e:
            production.update(state="failed", error=f"{type(e).__name__}: {str(e)[:200]}")
            extra = pick(TEXT["files_failed"], lang)
            _alert([f"client {client_id}: approved design {item_id} — slides not saved ({type(e).__name__}: {str(e)[:150]})"])
    social_queue.update_design(client_id, item_id, {"saved": {"name": saved["name"], "version": saved["version"]},
                                                    "production": production})
    _tell(client_id, item, pick(TEXT["approved"], lang).replace("{name}", saved["name"])
          .replace("{version}", str(saved["version"])).replace("{extra}", extra))
    return {"ok": True, **production}


def _finalize(client_id: int, item: dict, saved: dict) -> list:
    """Every slide of the set from the ONE approved spec + ONE palette → PNG →
    the client's Drive → a Social item, approved (these pixels were approved)."""
    d = item["design"]
    label = d["palette"]["label"]
    spec = saved["spec"]
    if spec.get("palette_variant") != label:
        raise RuntimeError(f"template palette {spec.get('palette_variant')!r} ≠ set palette {label!r}")
    roles = slide_spec.palette_roles(client_id, label)
    pngs = slide_renderer.render_set(spec, roles, d["slides"], loader_for(client_id))
    made = []
    for slide, png in zip(d["slides"], pngs):
        name = f"{d.get('set_id') or 'slides'}-{slide['asset_id']}-v{saved['version']}.png"
        uploaded = upload_png(client_id, name, png)
        ch = slide.get("character") or {}
        added = social_queue.add(
            client_id, uploaded["id"], "image", item.get("channels") or ["instagram"], caption=item.get("caption", ""),
            planned_date=item.get("planned_date", ""), producer="media", source="slide",
            title=f"{item.get('title') or d.get('set_id')} · {slide.get('n') or ''}/{slide.get('total') or ''}",
            post_ref={"post_id": d.get("post_id", ""), "set_id": d.get("set_id", ""), "asset_id": slide["asset_id"],
                      "n": slide.get("n"), "of": slide.get("total"),
                      "template": saved["name"], "template_version": saved["version"], "palette": label},
            characters=[ch] if ch.get("pose_id") else None,
            flags=[f for f in item.get("flags") or [] if f in ("ai_person_forbidden", "ai_label")] or None)
        social_queue.approve(client_id, [added["id"]], by="client:design")
        made.append({"asset_id": slide["asset_id"], "queue_id": added["id"], "file_id": uploaded["id"]})
    return made


def upload_png(client_id: int, name: str, png: bytes) -> dict:
    from core import drive_storage
    return drive_storage.scoped(_upload_scoped)(client_id, name, png)


def _upload_scoped(client_id: int, name: str, png: bytes) -> dict:
    from agents import media_agent
    from core import drive_service as drive
    folder = media_agent._subfolder(client_id, "images", "instagram")
    return drive.upload_bytes(folder, name, png, "image/png")


def render_asset(client_id: int, brief, palette: dict) -> dict:
    """ONE text_slide asset outside a set (media_agent.generate_from_brief):
    the client's template for this palette (else the starter) → PNG → Drive."""
    spec, name, based = base_spec(client_id, palette or {}, brief.aspect_ratio or "4:5")
    roles = slide_spec.palette_roles(client_id, slide_spec.variant_for(palette))
    entry = {"asset_id": brief.asset_id, "role": brief.role, "visual_direction": brief.visual_direction,
             "characters": list(brief.characters or []), "character_mode": brief.character_mode}
    (entry,), missing = attach_characters([entry])
    warnings = []
    png = slide_renderer.render_slide(spec, roles, brief.copy_headline, brief.copy_body, 1, 1, brief.role,
                                      loader_for(client_id), character=entry.get("character"), warnings=warnings)
    uploaded = upload_png(client_id, f"{brief.set_id}-{brief.asset_id}.png", png)
    out = {"success": True, "kind": "image", "file_id": uploaded.get("id"), "link": uploaded.get("webViewLink", ""),
           "credits": 0, "template": based}
    if entry.get("character"):
        out["characters"] = [entry["character"]]
    if warnings or missing:
        out["warnings"] = [w["code"] for w in warnings] + (["character_unavailable"] if missing else [])
    return out


def preview_png(client_id: int, item_id: str, n: int) -> bytes:
    """Slide n (1-based) of a design item, rendered now. RenderError /
    QueueError on failure."""
    item = social_queue.get(client_id, item_id)
    if not item or item.get("kind") != social_queue.DESIGN_KIND:
        raise social_queue.QueueError("not_found")
    d = item["design"]
    slides = d.get("slides") or []
    if not 1 <= int(n) <= len(slides):
        raise social_queue.QueueError("not_found")
    s = slides[int(n) - 1]
    roles = slide_renderer.slide_roles(slide_spec.palette_roles(client_id, d["palette"]["label"]), s.get("colors"))
    return slide_renderer.render_slide(d["spec"], roles, s.get("copy_headline", ""), s.get("copy_body", ""),
                                       int(s.get("n") or n), int(s.get("total") or len(slides)), s.get("role", ""),
                                       loader_for(client_id))


# ─── The spec in words (the card) ────────────────────────────────────────────

def describe(spec: dict, lang: str = "he") -> list:
    """The spec as short readable lines — label in the client's language, the
    design values as the spec names them."""
    out = []
    c = spec.get("canvas") or {}
    out.append(f"{pick(SPEC_LABELS['canvas'], lang)}: {c.get('width')}×{c.get('height')}")
    bg = spec.get("background") or {}
    parts = [bg.get("type", "")]
    for key in ("color_role", "second_color_role", "split_direction", "gradient_direction"):
        if bg.get(key):
            parts.append(str(bg[key]))
    if bg.get("type") == "split" and bg.get("split_ratio"):
        parts.append(f"{round(bg['split_ratio'] * 100)}%")
    if bg.get("type") == "image":
        parts.append(f"scrim {bg.get('scrim_opacity') or 0}")
    out.append(f"{pick(SPEC_LABELS['background'], lang)}: {' · '.join(p for p in parts if p)}")
    for block in spec.get("text_blocks") or []:
        label = pick(SPEC_LABELS.get(block["role"], SPEC_LABELS["body"]), lang)
        out.append(f"{label}: {block['font_family']} {block['weight']} · {block['size']}px · {block['color_role']} · "
                   f"{block.get('align') or 'right'} · {block['anchor']} {block['y_pct']}% · "
                   f"w {block['max_width_pct']}%" + (f" · h {block['max_height_pct']}%" if block.get("max_height_pct") else ""))
    accent = spec.get("accent") or {}
    if accent.get("type") and accent["type"] != "none":
        out.append(f"{pick(SPEC_LABELS['accent'], lang)}: {accent['type']} · {accent.get('position', '')} · "
                   f"{accent.get('size', '')}px · {accent.get('color_role') or 'accent'}")
    if (spec.get("logo") or {}).get("show"):
        out.append(f"{pick(SPEC_LABELS['logo'], lang)}: {spec['logo'].get('position', 'top_left')} · "
                   f"{spec['logo'].get('size_pct', 12)}%")
    counter = spec.get("counter") or {}
    if counter.get("show"):
        out.append(f"{pick(SPEC_LABELS['counter'], lang)}: {counter.get('style', 'fraction')} · "
                   f"{counter.get('position', 'bottom_left')}")
    m = spec.get("margins", 64)
    out.append(f"{pick(SPEC_LABELS['margins'], lang)}: " + (f"{m}px" if not isinstance(m, dict) else
                                                           " / ".join(f"{m[k]}" for k in ("top", "right", "bottom", "left"))))
    if spec.get("role_overrides"):
        out.append(f"{pick(SPEC_LABELS['overrides'], lang)}: {', '.join(spec['role_overrides'])}")
    return out


def view(item: dict, lang: str) -> dict:
    """The design part of a Social tab row."""
    d = item.get("design") or {}
    slides = d.get("slides") or []
    rev = d.get("rev", 1)
    problems = d.get("problems") or []
    return {"template_name": d.get("template_name", ""), "rev": rev, "palette": (d.get("palette") or {}).get("label", ""),
            "state": d.get("state", "ready"), "state_text": pick(STATE_TEXT.get(d.get("state", "ready"), STATE_TEXT["ready"]), lang),
            "previews": [f"/api/client/social/{item['id']}/slide/{i}?rev={rev}" for i in range(1, len(slides) + 1)],
            "slides": [{"n": s.get("n") or i, "role": s.get("role", ""), "copy_headline": s.get("copy_headline", ""),
                        "copy_body": s.get("copy_body", "")} for i, s in enumerate(slides, 1)],
            "spec_lines": describe(d.get("spec") or {}, lang), "spec": copy.deepcopy(d.get("spec") or {}),
            "rationale": d.get("rationale", ""), "critiques": (d.get("critiques") or [])[-5:],
            "problems": problem_text(problems, lang) if problems else "",
            # which slide, which field, by how much (core/copy_budget)
            "problem_lines": _problem_lines(problems, lang),
            "last_failure": problem_text(d["last_failure"], lang) if d.get("last_failure") else "",
            "based_on": d.get("based_on") or {}, "saved": d.get("saved") or {},
            "production": d.get("production") or {}, "stage_text": pick(TEXT["stage"], lang)}


# ─── Chat line ───────────────────────────────────────────────────────────────

def _lang(client_id: int) -> str:
    try:
        from core import agent_actions
        return agent_actions._lang(client_id)
    except Exception:
        return "he"


def _tell(client_id: int, item: dict, line: str):
    """One line in the media agent's chat. Never raises."""
    try:
        from agents.client_agent import log_communication
        log_communication(client_id, "outbound", f"dashboard_chat:{item.get('producer') or 'media'}", line)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: design line not posted ({type(e).__name__})")
