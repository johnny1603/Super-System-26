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
import time
from datetime import datetime, timezone

from core import post_brief, production_progress, social_formats, social_queue

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
    # The copy is longer than the current template holds (core/copy_budget):
    # one round to shorten it BEFORE the manager sees it
    "too_long": _five(
        "לפני שזה מגיע לאישור: חלק מהטקסט ארוך מדי לתבנית העיצוב הנוכחית ולא ייכנס בלי לשבור את כללי הקריאוּת:\n{lines}\nאני מקצר ומגיש שוב.",
        "Before this goes for approval: some of the copy is too long for the current design template and won't fit without breaking the legibility rules:\n{lines}\nI'm shortening it and submitting again.",
        "Avant validation : une partie du texte est trop longue pour le modèle actuel et ne tiendra pas sans enfreindre les règles de lisibilité :\n{lines}\nJe raccourcis et je soumets à nouveau.",
        "قبل الموافقة: جزء من النص أطول من قالب التصميم الحالي ولن يتسع دون كسر قواعد الوضوح:\n{lines}\nأختصره وأقدّمه من جديد.",
        "Перед одобрением: часть текста длиннее, чем вмещает текущий шаблон, и не поместится без нарушения правил читаемости:\n{lines}\nСокращаю и отправляю снова."),
    "queued_over": _five(
        " שימו לב: {k} שקופיות ארוכות מדי לתבנית הנוכחית — זה מסומן בכרטיס.",
        " Note: {k} slides are too long for the current template — it's marked on the card.",
        " À noter : {k} diapositives sont trop longues pour le modèle actuel — c'est indiqué sur la carte.",
        " انتبهوا: {k} شرائح أطول من القالب الحالي — هذا مُعلَّم في البطاقة.",
        " Обратите внимание: {k} слайдов длиннее текущего шаблона — это отмечено на карточке."),
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
    # An explicit colour instruction beats the brand palette (post_brief.clean_colors)
    "colors": _five(
        "לבקשתך, הצבעים בפוסט הזה לא מפלטת המותג ({requested}).",
        "As you asked, this post's colours are not the brand palette ({requested}).",
        "Comme demandé, les couleurs de ce post ne sont pas celles de la marque ({requested}).",
        "كما طلبتم، ألوان هذا المنشور ليست من ألوان العلامة ({requested}).",
        "По вашей просьбе цвета этого поста не из палитры бренда ({requested})."),
    # The AGENT's own reason for leaving the palette: said as a proposal the client decides on the design card
    "colors_agent": _five(
        "הצעתי בפוסט הזה צבעים שאינם בפלטת המותג, והסיבה: {requested}. זה יופיע על כרטיס העיצוב כחריגה, ואתם מחליטים אם לאשר אותה או לצייר בצבעי המותג.",
        "I proposed colours outside the brand palette for this post, because: {requested}. It will show on the design card as a deviation, and you decide whether to approve it or draw in the brand colours.",
        "J'ai proposé pour ce post des couleurs hors palette de marque, car : {requested}. Cela apparaîtra sur la carte du design comme un écart, et vous décidez de le valider ou de revenir aux couleurs de la marque.",
        "اقترحت لهذا المنشور ألوانًا خارج لوحة العلامة، والسبب: {requested}. سيظهر ذلك على بطاقة التصميم كخروج عن اللوحة، وأنتم تقررون اعتماده أو الرسم بألوان العلامة.",
        "Для этого поста я предложил цвета вне палитры бренда, причина: {requested}. На карточке дизайна это будет показано как отступление, и вы решаете — одобрить или нарисовать в цветах бренда."),
    # Production, said in the chat as it happens (2026-11-25): started, then
    # finished — or what failed and why. Never "done" when something failed.
    "started": _five(
        "התחלתי להפיק את הפוסט \"{title}\" ({n} חלקים). אכתוב כאן כשזה מסתיים — או מה נתקע, אם משהו נתקע.",
        "I've started producing the post \"{title}\" ({n} parts). I'll write here when it's done — or what got stuck, if anything does.",
        "J'ai commencé la production du post « {title} » ({n} parties). J'écrirai ici quand ce sera fini — ou ce qui bloque, le cas échéant.",
        "بدأت إنتاج المنشور \"{title}\" ({n} أجزاء). سأكتب هنا عند الانتهاء — أو ما تعثّر إن تعثّر شيء.",
        "Я начал производство поста «{title}» ({n} частей). Напишу здесь, когда закончу, — или что застряло, если застрянет."),
    # Approved twice (the Social tab, then "אישרתי" in chat): no second run
    "already_running": _five(
        "ההפקה של \"{title}\" כבר רצה — אין צורך לאשר שוב. {step}",
        "Production of \"{title}\" is already running — no need to approve again. {step}",
        "La production de « {title} » est déjà en cours — inutile de valider à nouveau. {step}",
        "إنتاج \"{title}\" جارٍ بالفعل — لا حاجة للموافقة مرة أخرى. {step}",
        "Производство «{title}» уже идёт — повторно одобрять не нужно. {step}"),
    "now_at": _five("כרגע: {step} ({pct}%).", "Right now: {step} ({pct}%).", "En ce moment : {step} ({pct} %).",
                    "الآن: {step} ({pct}%).", "Сейчас: {step} ({pct}%)."),
    # Never silence: approved, but production could not start — and why
    "start_failed": _five(
        "אישרתם את \"{title}\", אבל לא הצלחתי להתחיל את ההפקה: {reason}. הצוות קיבל התראה — אפשר לבקש ממני להריץ אותה שוב.",
        "You approved \"{title}\", but I couldn't start production: {reason}. The team was alerted — you can ask me to run it again.",
        "Vous avez validé « {title} », mais je n'ai pas pu lancer la production : {reason}. L'équipe est prévenue — vous pouvez me demander de la relancer.",
        "وافقتم على \"{title}\"، لكن لم أتمكن من بدء الإنتاج: {reason}. تم تنبيه الفريق — يمكنكم أن تطلبوا مني تشغيله مجددًا.",
        "Вы одобрили «{title}», но запустить производство не удалось: {reason}. Команда уведомлена — можно попросить меня запустить его снова."),
    "partial": _five(
        "הפקת הפוסט \"{title}\" לא הצליחה במלואה. מה שלא עבד:\n{problems}{ready}\nאפשר להריץ את ההפקה שוב — רק תגידו.",
        "Producing the post \"{title}\" did not fully work. What failed:\n{problems}{ready}\nI can run the production again — just say so.",
        "La production du post « {title} » n'a pas entièrement abouti. Ce qui a échoué :\n{problems}{ready}\nJe peux relancer la production — dites-le simplement.",
        "إنتاج المنشور \"{title}\" لم ينجح بالكامل. ما فشل:\n{problems}{ready}\nيمكنني تشغيل الإنتاج مجددًا — فقط قولوا.",
        "Производство поста «{title}» удалось не полностью. Что не получилось:\n{problems}{ready}\nМогу запустить производство снова — просто скажите."),
    # declared in the brief and absent from the render (core/render_manifest): the post FAILED
    "missing": _five(
        "הפוסט \"{title}\" נעצר ואי אפשר לאשר אותו כמו שהוא: משהו שהוגדר לו לא צויר.\n{problems}\nמה אפשר לעשות מופיע על הכרטיס שלו בלשונית סושיאל.",
        "The post \"{title}\" is on hold and cannot be approved as it is: something it was meant to have was not drawn.\n{problems}\nWhat you can do is on its card in the Social tab.",
        "Le post \"{title}\" est en attente et ne peut pas être validé tel quel : un élément prévu n'a pas été dessiné.\n{problems}\nCe que vous pouvez faire figure sur sa carte dans l'onglet Social.",
        "المنشور \"{title}\" متوقف ولا يمكن اعتماده كما هو: عنصر مطلوب له لم يُرسم.\n{problems}\nما يمكنكم فعله مذكور على بطاقته في تبويب السوشيال.",
        "Пост \"{title}\" приостановлен, и одобрить его в таком виде нельзя: заявленный элемент не нарисован.\n{problems}\nЧто можно сделать — на его карточке во вкладке «Соцсети»."),
    "not_drawn": _five("הדמות של {names} הוגדרה לתמונה ולא נמצאת בה", "{names}'s character was asked for and is not in the picture",
                       "le personnage de {names} était prévu et n'est pas dans l'image",
                       "شخصية {names} كانت مطلوبة وليست في الصورة", "персонаж {names} был заявлен, но его нет на изображении"),
    "unverified": _five(
        "הפקת הפוסט \"{title}\" הסתיימה, אבל לא הצלחתי לאמת מה בדיוק צויר בקבצים — ולכן אני לא מתאר אותם. הצוות קיבל הודעה.",
        "Producing the post \"{title}\" ended, but I could not verify what exactly was drawn in the files — so I am not describing them. The team was told.",
        "La production du post \"{title}\" est terminée, mais je n'ai pas pu vérifier ce qui a été dessiné — je ne le décris donc pas. L'équipe est prévenue.",
        "انتهى إنتاج المنشور \"{title}\"، لكن لم أستطع التحقق مما رُسم في الملفات — لذلك لا أصفها. أُبلغ الفريق.",
        "Производство поста \"{title}\" завершено, но проверить, что именно нарисовано, не удалось — поэтому я его не описываю. Команда в курсе."),
    "failed": _five(
        "הפקת הפוסט \"{title}\" נכשלה, ושום דבר ממנה לא מחכה לאישור. מה נכשל:\n{problems}\nאפשר להריץ את ההפקה שוב — רק תגידו.",
        "Producing the post \"{title}\" failed, and nothing from it is waiting for approval. What failed:\n{problems}\nI can run the production again — just say so.",
        "La production du post « {title} » a échoué, rien n'attend de validation. Ce qui a échoué :\n{problems}\nJe peux relancer la production — dites-le simplement.",
        "فشل إنتاج المنشور \"{title}\" ولا شيء منه بانتظار الموافقة. ما فشل:\n{problems}\nيمكنني تشغيل الإنتاج مجددًا — فقط قولوا.",
        "Производство поста «{title}» не удалось, ничего из него не ждёт одобрения. Что не получилось:\n{problems}\nМогу запустить производство снова — просто скажите."),
    "ready_part": _five(
        "\nמה שכן עבד ({what} פריטים) מחכה בלשונית סושיאל.", "\nWhat did work ({what} items) waits in the Social tab.",
        "\nCe qui a fonctionné ({what} éléments) attend dans l'onglet Social.", "\nما نجح ({what} عناصر) ينتظر في تبويب السوشيال.",
        "\nТо, что получилось ({what} шт.), ждёт во вкладке «Соцсети»."),
    "slide": _five("שקופית {n}", "Slide {n}", "Diapositive {n}", "الشريحة {n}", "Слайд {n}"),
    "design_failed": _five(
        " את שקופיות הטקסט לא הצלחתי לעצב בלי לשבור את כללי הקריאוּת — כדאי לקצר את הטקסט שלהן.",
        " I couldn't design the text slides without breaking the legibility rules — their copy should be shorter.",
        " Je n'ai pas pu concevoir les diapositives texte sans enfreindre les règles de lisibilité — leur texte doit être plus court.",
        " لم أتمكن من تصميم الشرائح النصية دون كسر قواعد الوضوح — يجب اختصار نصها.",
        " Не удалось оформить текстовые слайды, не нарушив правила читаемости, — их текст нужно сократить."),
}


# Why one asset was not made — plain words for the manager (status → line)
REASONS = {
    "no_account": _five("חשבון יצירת התמונות של העסק לא מחובר", "the business's image-generation account is not connected",
                        "le compte de génération d'images n'est pas connecté", "حساب توليد الصور غير متصل",
                        "аккаунт генерации изображений не подключён"),
    "context_missing": _five("קבצי הידע של המותג חסרים", "the brand's knowledge files are missing",
                             "les fichiers de connaissance de la marque manquent", "ملفات معرفة العلامة ناقصة",
                             "нет файлов знаний бренда"),
    "copy_leak": _five("הבקשה לתמונה נעצרה כי הכילה טקסט מהשקופית", "the image request was stopped: it carried the slide's text",
                       "la demande d'image a été arrêtée : elle contenait le texte", "أُوقف طلب الصورة لأنه احتوى نص الشريحة",
                       "запрос изображения остановлен: в нём был текст слайда"),
    "over_budget": _five("תקציב הקרדיטים של הפוסט נגמר", "the post's credit budget ran out",
                         "le budget de crédits du post est épuisé", "نفد رصيد المنشور", "бюджет кредитов поста исчерпан"),
    "render_failed": _five("ציור השקופית נכשל", "drawing the slide failed", "le rendu de la diapositive a échoué",
                           "فشل رسم الشريحة", "не удалось отрисовать слайд"),
    "design": _five("עיצוב שקופיות הטקסט נכשל", "designing the text slides failed",
                    "la conception des diapositives texte a échoué", "فشل تصميم الشرائح النصية",
                    "не удалось оформить текстовые слайды"),
    "failed": _five("ספק היצירה החזיר שגיאה", "the generation service returned an error",
                    "le service de génération a renvoyé une erreur", "أعادت خدمة التوليد خطأ",
                    "сервис генерации вернул ошибку"),
}
REASONS["renderer_unavailable"] = REASONS["render_failed"]
# Why production could not START (start_failed)
REASONS["storage"] = _five("לא הצלחתי לשמור את מצב ההפקה (תקלה זמנית באחסון)",
                           "I couldn't save the production's state (a temporary storage fault)",
                           "je n'ai pas pu enregistrer l'état de la production (panne de stockage temporaire)",
                           "تعذّر حفظ حالة الإنتاج (عطل مؤقت في التخزين)",
                           "не удалось сохранить состояние производства (временный сбой хранилища)")
# The file exists but never reached the approval queue (social_queue.add_safely returned nothing)
REASONS["not_queued"] = _five("הקובץ נוצר אבל לא נכנס לתור האישור (תקלה בשמירה) — הצוות קיבל הודעה",
                              "the file was made but did not reach the approval queue (a storage fault) — the team was told",
                              "le fichier a été créé mais n'est pas entré dans la file de validation (panne de stockage) — l'équipe est prévenue",
                              "أُنشئ الملف لكنه لم يدخل قائمة الاعتماد (عطل في الحفظ) — أُبلغ الفريق",
                              "файл создан, но не попал в очередь на одобрение (сбой хранилища) — команда в курсе")
REASONS["not_started"] = _five("משהו נכשל ברגע ההתחלה", "something failed at the moment it was starting",
                               "quelque chose a échoué au démarrage", "فشل شيء عند البدء", "что-то сломалось при запуске")


def reason_text(status: str, lang: str) -> str:
    return pick(REASONS.get(status) or REASONS["failed"], lang)


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
        if brief.characters:
            # An agent appears as its own character: the AI notices, shown only
            from core import agent_characters
            # (only the brief's own conflict: the AI-content notice is never stored from a
            # brief — the card rebuilds it from the render manifest, core/social_queue)
            flags[brief.asset_id] += [f for f in agent_characters.approval_flags(client_id, True) if f != "ai_label"]
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
    # The copy against the CURRENT template's real limits (core/copy_budget),
    # before the manager sees it: ONE round to shorten; then — or when the
    # agent says the idea needs more room (needs_room) — it goes to the card,
    # flagged per slide. Never a silent queue of copy that can't be drawn.
    fitted = {}
    if not revise:
        from core import copy_budget
        fitted = copy_budget.fit(client_id, palette, post["assets"], lang)
        over = [a for a in post["assets"] if a["asset_id"] in (fitted.get("over") or []) and not a.get("needs_room")]
        if over and not draft.get("length_round"):
            draft["length_round"] = 1
            draft["updated_at"] = _now()
            _save_drafts(client_id, drafts)
            lines = [line for a in over for line in fitted["slides"][a["asset_id"]]["lines"]]
            return {"refuse": pick(TEXT["too_long"], lang).replace("{lines}", "\n".join(f"• {l}" for l in lines)),
                    "code": "copy_too_long"}
    draft["updated_at"] = _now()
    _save_drafts(client_id, drafts)
    if revise:
        return {"refuse": pick(TEXT["revise"], lang).replace("{lines}", "\n".join(revise)), "code": "objection"}

    warning = post_brief.lead_time_warning(context["brand_facts"], post.get("occasion_date", ""),
                                           post.get("publish_date", ""))
    warnings = [{**warning, "occasion_date": post["occasion_date"]}] if warning else []
    # The lane is recorded now, on the item (core/governance §2): what the agent declared on
    # the post ("system" = its own plan), else a post asked for in a chat is the client's
    from core import governance
    declared = raw_post.get("lane") if isinstance(raw_post, dict) else None
    item = social_queue.add_post(client_id, post, palette=palette, warnings=warnings,
                                 escalations=escalations, flags=flags, sources=sources,
                                 producer=persona if persona in social_queue.PRODUCERS else "media",
                                 lane=governance.lane_at_creation(declared, "chat"))
    line = pick(TEXT["queued"], lang).replace("{n}", str(len(post["assets"])))
    if post.get("colors"):
        # The manager's explicit colours win over the brand palette — said, in one line
        line += " " + pick(TEXT["colors_agent" if post["colors"].get("by") == "agent" else "colors"], lang) \
            .replace("{requested}", post["colors"]["requested"])
    if escalations:
        line += " " + pick(TEXT["escalated"], lang)
    if fitted.get("over"):
        line += pick(TEXT["queued_over"], lang).replace("{k}", str(len(fitted["over"])))
    for w in social_queue.warning_lines(warnings, lang):
        line += "\n" + w
    return {"queued": item, "line": line}


def _positions(history: list, asset_id: str) -> dict:
    rows = [h for h in history if h.get("asset_id") == asset_id]
    last = rows[-1] if rows else {}
    return {"media": last.get("text", ""), "counterpart": last.get("response") or "kept the asset unchanged"}


# ─── 2-3. The owner approved the copy: production ────────────────────────────

def start(client_id: int, items: list, background_tasks=None, report: list = None) -> int:
    """After social_queue.approve: every approved POST item starts production
    (in the background when possible). Returns how many were started.
    In the chat (2026-11-25): the producer says it has started, the run shows
    as its progress card (the rerun_post_production action, started by the
    system on this approval), and the producer says how it ended.
    ONE path for every approval (the Social tab, the chat card, a typed yes):
    `report` (a list) gets one entry per post — {"item_id", "persona",
    "title", "state": started | already_running | failed_to_start, "line",
    "card"} — what the dashboard draws in that agent's chat AT ONCE, so the
    manager never has to tell the agent "אישרתי". A post whose production
    already runs is never started twice."""
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
        lang = _lang(client_id)
        running = running_card(client_id, item["id"], lang)
        if running:
            # Already producing (two approvals at once): no second run
            if report is not None:
                report.append(_entry(item, "already_running", already_line(item, running, lang), running))
            continue
        try:
            social_queue.set_production(client_id, item["id"], {"state": "running", "started_at": _now()})
        except Exception as e:
            entry = start_failed(client_id, item, "storage", e, lang)
            if report is not None:
                report.append(entry)
            continue
        # Right here, inside the approval's own request (2026-11-25): the line,
        # the card and the call that runs the pass in a request of its own take
        # a moment. Only the no-card fallback runs after the response.
        entry = run_in_chat(client_id, item["id"], background_tasks)
        if report is not None:
            report.append(entry)
        if entry.get("state") == "started":
            started += 1
    return started


def _entry(item: dict, state: str, line: str, card=None, reason: str = "") -> dict:
    return {"item_id": item.get("id", ""), "persona": item.get("producer") or "media", "title": item.get("title") or "",
            "state": state, "line": line, "card": card or None, "reason": reason}


def start_failed(client_id: int, item: dict, reason: str, error=None, lang: str = None) -> dict:
    """Production could not start: said in the producer's chat AT ONCE, with
    the reason, and the team alerted. Never raises."""
    lang = lang or _lang(client_id)
    line = (pick(TEXT["start_failed"], lang).replace("{title}", item.get("title") or "")
            .replace("{reason}", reason_text(reason, lang)))
    _say(client_id, item, line)
    _alert([f"client {client_id}: post {item.get('id')} approved but production did not start ({reason}"
            + (f": {type(error).__name__}: {str(error)[:150]}" if error else "") + ")"])
    try:
        social_queue.set_production(client_id, item["id"], {"state": "failed", "finished_at": _now(),
                                                            "problems": [f"• {reason_text(reason, lang)}"]})
    except Exception:
        pass                    # storage down: the line and the alert still went out
    return _entry(item, "failed_to_start", line, reason=reason)


def running_card(client_id: int, item_id: str, lang: str = "he") -> dict:
    """The card of this post's production while it runs (the rerun_post_production
    row, fresh — RERUN_STALE_MINUTES), else {}."""
    try:
        from core import agent_actions
        rows = (agent_actions._db().table(agent_actions.TABLE).select("*").eq("client_id", int(client_id))
                .eq("action", "rerun_post_production").eq("status", "running")
                .order("id", desc=True).limit(20).execute().data) or []
    except Exception:
        return {}
    for row in rows:
        if str((row.get("params") or {}).get("item_id")) != str(item_id):
            continue
        try:
            began = datetime.fromisoformat(str(row.get("decided_at")))
        except ValueError:
            continue
        if (datetime.now(timezone.utc) - began).total_seconds() < RERUN_STALE_MINUTES * 60:
            from core import agent_actions
            return agent_actions.card(row, "client", lang)
    return {}


def already_running(client_id: int, item_ids, lang: str = None) -> list:
    """start()'s report entries for posts among `item_ids` whose production
    runs right now (approved again: nothing new starts). Never raises."""
    out = []
    try:
        lang = lang or _lang(client_id)
        for item_id in item_ids or []:
            item = social_queue.get(client_id, str(item_id)) or {}
            if item.get("kind") != social_queue.POST_KIND or item.get("status") != "approved":
                continue
            card = running_card(client_id, item["id"], lang)
            if card:
                out.append(_entry(item, "already_running", already_line(item, card, lang), card))
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: running productions not read ({type(e).__name__})")
    return out


def running_now(client_id: int, lang: str = None, persona: str = None) -> list:
    """Every post production running right now (fresh), as report entries —
    for "אישרתי" typed after the approval in the Social tab."""
    try:
        ids = [i["id"] for i in social_queue.items(client_id)
               if i.get("kind") == social_queue.POST_KIND and i.get("status") == "approved"
               and (i.get("production") or {}).get("state") == "running"
               and (persona is None or (i.get("producer") or "media") == persona)]
    except Exception:
        return []
    return already_running(client_id, ids, lang)


def already_line(item: dict, card: dict, lang: str) -> str:
    """"Already running — no need to approve again", with where it is now."""
    progress = (card or {}).get("progress") or {}
    step = ""
    if progress.get("step"):
        step = (pick(TEXT["now_at"], lang).replace("{step}", progress["step"])
                .replace("{pct}", str(int(round(100 * float(progress.get("fraction") or 0))))))
    return pick(TEXT["already_running"], lang).replace("{title}", item.get("title") or "").replace("{step}", step).strip()


def run_in_chat(client_id: int, item_id: str, background_tasks=None) -> dict:
    """The started line, then production as a running card in the producer's
    chat — its whole pass inside a request of its own (agent_actions
    .run_in_own_request). Without the card (storage down) production still
    runs and says how it ended. Never raises. Returns start()'s report entry."""
    item = social_queue.get(client_id, item_id) or {"id": item_id}
    lang = _lang(client_id)
    n = len((item.get("post") or {}).get("slides") or [])
    line = pick(TEXT["started"], lang).replace("{title}", item.get("title") or "").replace("{n}", str(n))
    _say(client_id, item, line)
    try:
        from core import agent_actions
        row = agent_actions.start_system_run(client_id, "rerun_post_production", {"item_id": item_id},
                                             persona=item.get("producer") or "media")
        if row:
            card = agent_actions.card(row, "client", lang) if isinstance(row, dict) else None
            return _entry(item, "started", line, card)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: production card not started ({type(e).__name__})")
    try:
        if background_tasks is not None:
            background_tasks.add_task(produce, client_id, item_id)
        else:
            produce(client_id, item_id)
    except Exception as e:
        return start_failed(client_id, item, "not_started", e, lang)
    return _entry(item, "started", line)

GEN_PARALLEL = 4        # a post's generations at the vendor at once (each slot: one job, polled to its end)


def _stopped() -> bool:
    """The manager stopped this run (its card) — checked BETWEEN steps."""
    try:
        from core import agent_actions
        return agent_actions.stop_requested()
    except Exception:
        return False


def _generate_all(client_id: int, jobs: list, palette: dict, platform: str, budget: float) -> tuple:
    """Every non-text asset of ONE post submitted together (GEN_PARALLEL at a
    time) and waited for as a set (2026-11-25) — four images take about as
    long as one. The credit budget is kept: before a paid generation is
    submitted, what was spent plus what the generations still running
    reserved (their slide budgets) must be under the post's budget.
    A stop (the card) means nothing more is submitted; a generation already
    at the vendor finishes (paid). Returns ({n: result}, spent)."""
    import threading
    from agents import media_agent
    from core import parallel
    lock, gate = threading.Lock(), threading.Semaphore(GEN_PARALLEL)
    ledger = {"spent": 0.0, "reserved": 0.0}

    lang = _lang(client_id)

    def make(n, brief):
        with gate:
            if _stopped():
                return {"success": False, "status": "stopped"}
            paid = brief.asset_type.startswith("generated")
            own = float(brief.budget_credits or 0)
            if paid:
                with lock:
                    if budget and ledger["spent"] + ledger["reserved"] >= budget:
                        production_progress.done(f"generate:{n}", False, reason_text("over_budget", lang))
                        return {"success": False, "status": "over_budget"}
                    ledger["reserved"] += own
            production_progress.begin(f"generate:{n}")
            try:
                out = media_agent.generate_from_brief(client_id, brief, palette, platform=platform)
            except Exception as e:
                out = {"success": False, "status": "failed", "errors": [f"{type(e).__name__}: {str(e)[:200]}"]}
            if out.get("success"):
                production_progress.done(f"generate:{n}", skipped=bool(out.get("real_asset")))
            else:
                status = out.get("status") or "failed"
                production_progress.done(f"generate:{n}", False,
                                         out.get("message") if status.startswith("character") and out.get("message")
                                         else reason_text(status, lang))
            if paid:
                with lock:
                    ledger["reserved"] -= own
                    if out.get("success"):
                        ledger["spent"] += float(out.get("credits") or 0)
            return out
    with media_agent.one_post_choices():          # the real-file / pose choice: one slide at a time
        got = parallel.gather({n: (lambda n=n, b=brief: make(n, b)) for n, _slide, brief in jobs})
    out = {n: (value if ok else {"success": False, "status": "failed",
                                 "errors": [f"{type(value).__name__}: {str(value)[:200]}"]})
           for n, (ok, value) in got.items()}
    return out, ledger["spent"]


def produce(client_id: int, item_id: str, tell: bool = True) -> dict:
    """Every asset of an approved post, within its credit budget — the
    generations submitted together and waited for as a set, then the slides
    designed (their character poses made together too). ONE pass, never
    paced by a tick. Never raises; the result is recorded on the post item.
    `tell` = post the outcome line in the producer's chat (the rerun action
    posts it itself). Every failure is recorded as a plain line
    (production["problems"]) — what failed and why — and the line never says
    "done" when something failed. Stopped (the card): nothing new starts;
    what was already made is kept as a draft and recorded (state "stopped")."""
    item = social_queue.get(client_id, item_id)
    if not item or item.get("kind") != social_queue.POST_KIND or item.get("status") != "approved":
        return {"state": "failed", "reason": "not_an_approved_post"}
    # The real step list, known now (core/production_progress): the bar
    # every card and the Social tab show, from the first second to the end
    tracker = production_progress.start(client_id, item_id, (item.get("post") or {}).get("slides") or [])
    try:
        from core import governance
        with governance.lane_scope(item.get("lane")):      # its assets and its costs carry the post's lane
            production = _produce(client_id, item, tell)
    except Exception as e:
        tracker.finish("failed")
        print(f"[{SERVICE_NAME}] client {client_id}: production broke ({type(e).__name__}: {str(e)[:200]})")
        raise
    finally:
        production_progress.stop_tracking()
    tracker.finish(production["state"])
    return production


def _produce(client_id: int, item: dict, tell: bool) -> dict:
    item_id = item["id"]
    body = item.get("post") or {}
    slides = body.get("slides") or []
    palette = body.get("palette") or {}
    budget = sum(int(s.get("budget_credits") or 0) for s in slides)
    from core import render_manifest
    spent, made, not_made, failed, results = 0.0, 0, 0, 0, []
    text_slides, character_errors, problems, lacking = [], [], [], []
    lang = _lang(client_id)
    colors = (body.get("colors") or {}).get("slides") or []
    clock = time.monotonic()
    timing = {}
    jobs = []
    for n, slide in enumerate(slides, 1):
        if slide["asset_type"] == "text_slide":
            # Drawn by the slide renderer as ONE set (one template, one palette
            # variant) — never an image model. The design waits for approval.
            entry = {**slide, "n": n, "total": len(slides)}
            if len(colors) >= n:
                entry["colors"] = colors[n - 1]             # colours stated for this slide
                # why they may leave the brand palette — no reason = the design card FAILS (core/slide_design)
                entry["colors_reason"] = (body.get("colors") or {}).get("reason") or (body.get("colors") or {}).get("requested", "")
            text_slides.append(entry)
            continue
        brief = post_brief.AssetBrief(
            asset_id=slide["asset_id"], post_format=body["post_format"], objective=body["objective"],
            client_id=int(client_id), role=slide["role"],
            # The APPROVED copy, carried for context only — generate_from_brief
            # never puts it in a prompt (assert_copy_free checks it)
            copy_headline=slide.get("copy_headline", ""), copy_body=slide.get("copy_body", ""),
            asset_type=slide["asset_type"], visual_direction=slide.get("visual_direction", ""),
            must_include=list(slide.get("must_include") or []), must_avoid=list(slide.get("must_avoid") or []),
            aspect_ratio=slide.get("aspect_ratio") or social_formats.aspect_for_post(
                body["post_format"], body.get("placement", "")),
            safe_zone=slide.get("safe_zone") or "", set_id=body["set_id"],
            budget_credits=int(slide.get("budget_credits") or 0),
            characters=list(slide.get("characters") or []), character_mode=slide.get("character_mode") or "")
        jobs.append((n, slide, brief))
    generated, _ = _generate_all(client_id, jobs, palette, (item["channels"] or ["instagram"])[0], budget) \
        if jobs else ({}, 0.0)
    timing["generate_s"] = round(time.monotonic() - clock, 1)
    blocked = False                     # no account / no context: said once, like the sequential run did
    stopped = False
    for n, slide, brief in jobs:
        out = generated.get(n) or {"success": False, "status": "failed"}
        if out.get("status") == "stopped":
            stopped = True
            results.append({"asset_id": brief.asset_id, "status": "stopped"})
            not_made += 1
            continue
        if out.get("status") == "over_budget":
            results.append({"asset_id": brief.asset_id, "status": "over_budget"})
            problems.append(_problem(n, reason_text("over_budget", lang), lang))
            not_made += 1
            continue
        if not out.get("success"):
            status = out.get("status") or "failed"
            if blocked and status in ("no_account", "context_missing"):
                continue                # the same cause as the slide above — not a second line
            results.append({"asset_id": brief.asset_id, "status": status})
            if out.get("message") and status.startswith("character"):
                character_errors.append(out["message"])       # said to the manager, not only logged
            if status in (post_brief.RENDERER_NOT_IMPLEMENTED, "needs_real_asset"):
                not_made += 1
            else:
                failed += 1
                problems.append(_problem(n, out.get("message") if status.startswith("character") and out.get("message")
                                         else reason_text(status, lang), lang))
                if status in ("no_account", "context_missing"):
                    blocked = True      # the next assets fail the same way
            continue
        spent += float(out.get("credits") or 0)
        # The MANIFEST of what was made (core/render_manifest) — everything said
        # about this asset from here on is built from it, never from the brief
        drawn = out.get("manifest") or render_manifest.for_asset(out.get("kind") or "image", out)
        shown = render_manifest.characters(drawn)
        absent = [c for c in brief.characters if c not in shown]
        if absent:
            # declared and not in the result: this asset FAILED — it is not queued, not "made"
            from core import agent_characters
            line = _problem(n, pick(TEXT["not_drawn"], lang).replace(
                "{names}", ", ".join(agent_characters.display_name(c, lang) for c in absent)), lang)
            for c in absent:
                render_manifest.trace("composited", c, "NOT DRAWN: declared in the brief, absent from the result", n, brief.asset_id)
            lacking += [{"type": "character", "id": c, "slide": n, "asset_id": brief.asset_id, "reason": "not_drawn",
                         "line": line[2:]} for c in absent]
            results.append({"asset_id": brief.asset_id, "status": "element_missing"})
            problems.append(line)
            failed += 1
            continue
        # A real asset from the client's folder is not a concept image
        flags = ["concept"] if "concept" in (slide.get("flags") or []) and not out.get("real_asset") else []
        flags += [f for f in slide.get("flags") or [] if f == "ai_person_forbidden" and shown]
        flags += render_manifest.disclosure(drawn)          # iff a generated element is in it
        if out.get("low_resolution"):
            flags.append("low_resolution")         # shown on the card; the alert was raised where it was measured
        added = social_queue.add_safely(
            client_id, out["file_id"], out.get("kind") or "image",
            [c for c in item["channels"] if (out.get("kind") or "image") in social_queue.CHANNEL_KINDS.get(c, ())]
            or ["instagram"],
            caption=item.get("caption", ""), planned_date=item.get("planned_date", ""), producer="media",
            source="post", title=f"{item.get('title') or body['set_id']} · {n}/{len(slides)}", flags=flags,
            post_ref={"post_id": item["id"], "set_id": body["set_id"], "asset_id": brief.asset_id,
                      "n": n, "of": len(slides),
                      "format": social_formats.for_post(body["post_format"], body.get("placement", "")).name,
                      "placement": body.get("placement", "")},
            real_asset=out.get("real_asset") or None, characters=out.get("characters") or None, manifest=drawn)
        if not added.get("id"):
            # It never reached the approval queue (add_safely alerted why): NOT "made" —
            # an item nobody can approve must never be counted as produced
            results.append({"asset_id": brief.asset_id, "status": "not_queued"})
            problems.append(_problem(n, reason_text("not_queued", lang), lang))
            failed += 1
            continue
        results.append({"asset_id": brief.asset_id, "status": "made", "queue_id": added.get("id", ""),
                        "link": out.get("link", ""), "source": "real_asset" if out.get("real_asset") else "generated",
                        "drawn": render_manifest.facts(drawn)})
        made += 1
    design = {}
    if text_slides and (stopped or _stopped()):
        # Stopped before the design: no slides drawn, no poses generated
        stopped = True
        results += [{"asset_id": s["asset_id"], "status": "stopped"} for s in text_slides]
        not_made += len(text_slides)
        text_slides = []
    if text_slides:
        from core import slide_design
        design_clock = time.monotonic()
        opened = slide_design.open_for_post(client_id, item, text_slides, lang)
        timing["design_s"] = round(time.monotonic() - design_clock, 1)    # poses + layout + render
        drawn_without = list(dict.fromkeys(opened.get("character_errors") or []))
        character_errors += drawn_without
        absent = opened.get("missing") or []            # declared in the brief, absent from the render
        lacking += absent
        # each missing element by name, with its reason (the line already carries the why)
        problems += [f"• {m['line']}" for m in absent] if absent else [f"• {m}" for m in drawn_without]
        design = {"item_id": (opened.get("item") or {}).get("id", ""), "slides": len(text_slides),
                  "ok": bool(opened.get("item")) and not opened.get("problems") and not absent}
        by_asset = {m.get("asset_id"): m for m in opened.get("manifests") or []}
        gone = {m.get("asset_id") for m in absent}
        results += [{"asset_id": s["asset_id"],
                     "status": "element_missing" if s["asset_id"] in gone else "design_waiting" if design["item_id"] else "failed",
                     **({"drawn": render_manifest.facts(by_asset[s["asset_id"]])} if s["asset_id"] in by_asset else {})}
                    for s in text_slides]
        # WHICH slide, WHICH field, by HOW MUCH — never only "design failed"
        from core import copy_budget
        said = [f"• {l}" for l in copy_budget.problem_lines(
            [p for p in opened.get("problems") or [] if p.get("slide")], lang)]
        if not design["item_id"]:
            failed += len(text_slides)
            problems += said or [f"• {reason_text('design', lang)}"]
        elif not design["ok"] and not absent:      # (a missing element is already named above)
            problems += said or [f"• {reason_text('design', lang)}"]
    production_progress.begin("assemble")
    state = ("done" if (made or design.get("item_id")) and not failed else "partial" if made or design.get("item_id")
             else "failed" if failed else "done")
    if state == "done" and problems:
        state = "partial"
    if lacking:
        # The brief declared something the render lacks: FAILED, by name — never a
        # "partial" that reads like completion (knowledge/client-governance.md §8)
        state = "failed"
    if stopped or _stopped():
        state = "stopped"             # never left "running"; what was made stays a draft (recorded above)
    production = {"state": state, "made": made, "not_made": not_made, "failed": failed,
                  "credits": round(spent, 2), "assets": results, "finished_at": _now(), "design": design,
                  "character_errors": list(dict.fromkeys(character_errors)),
                  "problems": list(dict.fromkeys(problems)), "missing": lacking,
                  # measured on every run (2026-11-25): where the time went
                  "timing": {**timing, "total_s": round(time.monotonic() - clock, 1), "parallel": GEN_PARALLEL}}
    social_queue.set_production(client_id, item_id, production)
    production_progress.done("assemble")
    if tell:
        _tell(client_id, item, production)
    if failed or problems:
        _alert([f"client {client_id}: post {body.get('set_id')} — production {state}: " + " | ".join(problems)[:600]])
    return production


def _lang(client_id: int) -> str:
    try:
        from core import agent_actions
        return agent_actions._lang(client_id)
    except Exception:
        return "he"


def _problem(n: int, reason: str, lang: str) -> str:
    return f"• {pick(TEXT['slide'], lang).replace('{n}', str(n))}: {reason}"


def summary(item: dict, production: dict, lang: str) -> str:
    """The ONE line about how production ended: "done" only when nothing
    failed; otherwise what failed and why, what did work, and the offer to
    run it again."""
    title = item.get("title") or ""
    problems = production.get("problems") or []
    state = production.get("state")
    if production.get("missing"):
        return pick(TEXT["missing"], lang).replace("{title}", title).replace("{problems}", "\n".join(problems) or "•")
    if state in ("partial", "failed") or problems:
        ready = ""
        design = production.get("design") or {}
        if production.get("made") or design.get("item_id"):
            count = int(production.get("made") or 0) + (int(design.get("slides") or 0) if design.get("item_id") else 0)
            ready = pick(TEXT["ready_part"], lang).replace("{what}", str(count))
        table = TEXT["failed"] if state == "failed" and not ready else TEXT["partial"]
        return (pick(table, lang).replace("{title}", title).replace("{problems}", "\n".join(problems) or "•")
                .replace("{ready}", ready))
    extra = pick(TEXT["not_made"], lang).replace("{n}", str(production["not_made"])) if production.get("not_made") else ""
    design = production.get("design") or {}
    if design.get("item_id"):
        extra += pick(TEXT["design" if design.get("ok") else "design_failed"], lang).replace("{n}", str(design["slides"]))
    return (pick(TEXT["produced"], lang).replace("{title}", title)
            .replace("{made}", str(production.get("made") or 0)).replace("{extra}", extra))


def _say(client_id: int, item: dict, line: str):
    """One line in the producer's chat. Never raises."""
    try:
        from agents.client_agent import log_communication
        log_communication(client_id, "outbound", f"dashboard_chat:{item.get('producer') or 'media'}", line)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: production line not posted ({type(e).__name__})")


def unbacked(production: dict) -> list:
    """Assets the record calls made / designed WITHOUT a manifest behind them."""
    return [a.get("asset_id", "") for a in production.get("assets") or []
            if a.get("status") in ("made", "design_waiting") and "drawn" not in a]


def announce(client_id: int, item: dict, production: dict, lang: str) -> str:
    """The line about a finished production — written ONLY with the manifest
    in hand: when an asset is called made and nothing records what was drawn
    in it, the line says exactly that (and the team is alerted) instead of
    describing the post."""
    loose = unbacked(production)
    if loose:
        _alert([f"client {client_id}: post {(item.get('post') or {}).get('set_id') or item.get('id')} — no render "
                f"manifest for {', '.join(loose)}; the production line does not describe the post"])
        return pick(TEXT["unverified"], lang).replace("{title}", item.get("title") or "")
    return summary(item, production, lang)


def _tell(client_id: int, item: dict, production: dict):
    """How production ended, in the producer's chat. Never raises."""
    try:
        _say(client_id, item, announce(client_id, item, production, _lang(client_id)))
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: production line not posted ({type(e).__name__})")


# ─── Run again (the rerun_post_production action) ────────────────────────────

RERUN_STALE_MINUTES = 30


def rerun_refusal(client_id: int, item_id: str) -> str:
    """"" when this post may be produced again, else why not (English, for
    the agent): an APPROVED post whose production is not running right now."""
    item = social_queue.get(client_id, item_id)
    if not item or item.get("kind") != social_queue.POST_KIND:
        return "not a post in the Social tab"
    if item.get("status") != "approved":
        return "its copy is not approved yet — production starts when the manager approves it"
    prod = item.get("production") or {}
    if prod.get("state") == "running":
        try:
            started = datetime.fromisoformat(str(prod.get("started_at")))
            if (datetime.now(timezone.utc) - started).total_seconds() < RERUN_STALE_MINUTES * 60:
                return "its production is running right now"
        except ValueError:
            pass
    return ""


def retryable(client_id: int, limit: int = 10) -> list:
    """Approved posts whose production failed, partly failed, was stopped or
    is stuck — what the agents must tell the manager about (and may offer to rerun)."""
    out = []
    for item in social_queue.items(client_id):
        if item.get("kind") != social_queue.POST_KIND or item.get("status") != "approved":
            continue
        prod = item.get("production") or {}
        if prod.get("state") in ("failed", "partial", "stopped") or (prod.get("state") == "running"
                                                           and not rerun_refusal(client_id, item["id"])):
            out.append({"item_id": item["id"], "title": item.get("title", ""), "state": prod.get("state"),
                        "problems": prod.get("problems") or [], "finished_at": prod.get("finished_at", "")})
    return out[-limit:]


def rerun(client_id: int, item_id: str) -> dict:
    """Production again for an approved post (the manager said yes in chat,
    or the system started it on the approval). Returns the production record
    + "text": the line the card posts."""
    item = social_queue.get(client_id, item_id) or {}
    social_queue.set_production(client_id, item_id, {"state": "running", "started_at": _now()})
    production = produce(client_id, item_id, tell=False)
    return {**production, "text": announce(client_id, item, production, _lang(client_id))}