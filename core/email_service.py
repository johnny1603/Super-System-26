import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# The app password previously hardcoded here leaked into git history — it must be
# rotated in the Google account and supplied via the GMAIL_APP_PASSWORD env var.
GMAIL_USER = os.environ.get("GMAIL_USER", "johnny_support@uallak.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", GMAIL_USER)
PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "https://uallak.com")

# ─── Client-facing email language ──────────────────────────────────────────────
# Unlike chat (which detects language live from the client's own message),
# outbound emails have no message to detect from - they use a STORED
# preference instead (clients.language, same 5 codes as dashboard/assets/
# i18n.js's engine; captured at checkout from the sales-chat page's active
# language, kept in sync by the profile page's switcher). Internal/team
# emails (send_admin_alert, the weekly platform digests) are NOT part of
# this - Johnny reads Hebrew, same rule as the admin dashboard.
_LANG_DIR = {"he": "rtl", "en": "ltr", "fr": "ltr", "ar": "rtl", "ru": "ltr"}


def _lang_of(client_id: int = None, language: str = None) -> str:
    """Resolves which language to render a client-facing email in: an
    explicit override first (e.g. the sales-chat page's active language,
    passed in before a client row even exists), else the client's stored
    preference, else Hebrew. Never raises - a lookup failure just falls
    back to the default language rather than blocking the send."""
    if language in _LANG_DIR:
        return language
    if client_id:
        try:
            from agents.client_agent import get_client
            stored = (get_client(client_id) or {}).get("language")
            if stored in _LANG_DIR:
                return stored
        except Exception as e:
            print(f"[email_service] language lookup failed (non-fatal): {e}")
    return "he"


def _tr(table: dict, key: str, lang: str, **vars) -> str:
    """Same {name}-placeholder convention as dashboard/assets/i18n.js's t() -
    Hebrew is the source of truth and the fallback for any missing key."""
    entry = table.get(key) or {}
    text = entry.get(lang)
    if text is None:
        text = entry.get("he", "")
    for name, value in vars.items():
        text = text.replace("{" + name + "}", str(value))
    return text

_CLIENT_REPORT_I18N = {
    "subtitle": {"he":"הבית לעסקים קטנים ובינוניים","en":"A home for small and medium businesses","fr":"Une maison pour les PME","ar":"بيت للشركات الصغيرة والمتوسطة","ru":"Дом для малого и среднего бизнеса"},
    "greeting": {"he":"שלום {name} 👋","en":"Hello {name} 👋","fr":"Bonjour {name} 👋","ar":"مرحبًا {name} 👋","ru":"Здравствуйте, {name} 👋"},
    "greeting_body": {"he":"המערכת סיימה לנתח את העסק שלך ובנתה תכנית עבודה מותאמת אישית.","en":"The system has finished analyzing your business and built a personalized work plan.","fr":"Le système a terminé d'analyser votre entreprise et a élaboré un plan de travail personnalisé.","ar":"انتهى النظام من تحليل عملك وأعدّ خطة عمل مخصصة.","ru":"Система завершила анализ вашего бизнеса и составила персональный план работы."},
    "summary_title": {"he":"📋 תמונת מצב","en":"📋 Situation summary","fr":"📋 État des lieux","ar":"📋 لمحة عن الوضع","ru":"📋 Обзор ситуации"},
    "market_title": {"he":"📊 תמונת השוק שלך","en":"📊 Your market picture","fr":"📊 Votre situation de marché","ar":"📊 صورة سوقك","ru":"📊 Картина вашего рынка"},
    "goals_title": {"he":"🎯 היעדים שלנו ל-90 יום","en":"🎯 Our 90-day goals","fr":"🎯 Nos objectifs à 90 jours","ar":"🎯 أهدافنا لـ90 يومًا","ru":"🎯 Наши цели на 90 дней"},
    "goals_sub": {"he":"הערכות מבוססות ניסיון ונתוני שוק — לא הבטחות מדויקות","en":"Estimates based on experience and market data — not exact promises","fr":"Estimations basées sur l'expérience et les données du marché — pas des promesses exactes","ar":"تقديرات مبنية على الخبرة وبيانات السوق — وليست وعودًا دقيقة","ru":"Оценки на основе опыта и рыночных данных — не точные обещания"},
    "packages_title": {"he":"💰 המסלולים שלך לבחירה","en":"💰 Your plans to choose from","fr":"💰 Vos formules au choix","ar":"💰 باقاتك للاختيار","ru":"💰 Пакеты на выбор"},
    "setup_label": {"he":"עלות הקמה:","en":"Setup cost:","fr":"Frais de mise en place :","ar":"تكلفة التأسيس:","ru":"Стоимость настройки:"},
    "monthly_label": {"he":"דמי ניהול חודשיים:","en":"Monthly management fee:","fr":"Frais de gestion mensuels :","ar":"رسوم الإدارة الشهرية:","ru":"Ежемесячная плата за ведение:"},
    "benefit_label": {"he":"🎁 הטבה: 2 חודשי ניהול חינם — שווי ₪{value}","en":"🎁 Bonus: 2 free months of management — worth ₪{value}","fr":"🎁 Avantage : 2 mois de gestion offerts — d'une valeur de ₪{value}","ar":"🎁 مزايا: شهران مجانيان من الإدارة — بقيمة ₪{value}","ru":"🎁 Бонус: 2 бесплатных месяца ведения — на сумму ₪{value}"},
    "cta_title": {"he":"מוכן להתחיל?","en":"Ready to start?","fr":"Prêt à démarrer ?","ar":"هل أنت مستعد للبدء؟","ru":"Готовы начать?"},
    "cta_button": {"he":"התחל עכשיו →","en":"Start now →","fr":"Commencer maintenant →","ar":"ابدأ الآن ←","ru":"Начать сейчас →"},
    "subject": {"he":"uallak — התכנית שלך מוכנה! 🚀","en":"uallak — Your plan is ready! 🚀","fr":"uallak — Votre plan est prêt ! 🚀","ar":"uallak — خطتك جاهزة! 🚀","ru":"uallak — Ваш план готов! 🚀"},
}


def send_client_report(client_email: str, client_name: str, proposal: dict, language: str = None):
    p = proposal
    lang = _lang_of(language=language)  # no client row exists yet at proposal time
    t = lambda key, **v: _tr(_CLIENT_REPORT_I18N, key, lang, **v)
    packages = p.get('packages', [])
    packages_html = ''.join([f"""
        <div style="background:#252525;border-radius:10px;padding:20px;margin-bottom:12px;">
          <h4 style="margin:0 0 6px;color:#FFD166;">{pkg.get('name','')}</h4>
          <p style="color:rgba(255,255,255,0.75);margin:0 0 10px;font-size:14px;">{pkg.get('description','')}</p>
          <p style="margin:0;">{t('setup_label')} <strong style="color:#FFD166;">₪{pkg.get('setup_fee_total',0)}</strong> · {t('monthly_label')} <strong style="color:#FFD166;">₪{pkg.get('monthly_management_total',0)}</strong></p>
          <p style="color:#00C96E;margin:6px 0 0;">{t('benefit_label', value=pkg.get('benefit_value',0))}</p>
        </div>
    """ for pkg in packages])
    scarcity_html = f'<p style="text-align:center;color:#FF4C1F;font-weight:700;margin:0 0 20px;">🔥 {p.get("scarcity_note")}</p>' if p.get('scarcity_note') else ''

    html = f"""
    <div dir="{_LANG_DIR[lang]}" style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#F7F4EF;padding:32px;border-radius:16px;">
      <div style="text-align:center;margin-bottom:32px;">
        <h1 style="font-size:32px;font-weight:900;margin:0;">u<span style="color:#FF4C1F;">allak</span></h1>
        <p style="color:#8A8A8A;margin:4px 0 0;">{t('subtitle')}</p>
      </div>
      <div style="background:white;border-radius:12px;padding:28px;margin-bottom:20px;">
        <h2 style="margin:0 0 16px;">{t('greeting', name=client_name)}</h2>
        <p style="color:#3D3D3D;line-height:1.7;">{t('greeting_body')}</p>
      </div>
      <div style="background:white;border-radius:12px;padding:28px;margin-bottom:20px;">
        <h3 style="color:#FF4C1F;margin:0 0 16px;">{t('summary_title')}</h3>
        <p style="color:#3D3D3D;line-height:1.7;">{p.get('business_summary','')}</p>
      </div>
      {f'''<div style="background:white;border-radius:12px;padding:28px;margin-bottom:20px;">
        <h3 style="color:#FF4C1F;margin:0 0 16px;">{t('market_title')}</h3>
        <p style="color:#3D3D3D;line-height:1.7;">{p.get('market_reality','')}</p>
      </div>''' if p.get('market_reality') else ''}
      <div style="background:white;border-radius:12px;padding:28px;margin-bottom:20px;">
        <h3 style="color:#FF4C1F;margin:0 0 16px;">{t('goals_title')}</h3>
        <p style="color:#8A8A8A;font-size:12px;margin:0 0 10px;">{t('goals_sub')}</p>
        {''.join([f'<p style="margin:8px 0;">✅ {g}</p>' for g in p.get('goals_90_days',[])])}
      </div>
      <div style="background:#1A1A1A;border-radius:12px;padding:28px;margin-bottom:20px;color:white;">
        <h3 style="margin:0 0 16px;">{t('packages_title')}</h3>
        {packages_html}
      </div>
      {scarcity_html}
      <div style="background:#FF4C1F;border-radius:12px;padding:28px;text-align:center;">
        <h3 style="color:white;margin:0 0 16px;">{t('cta_title')}</h3>
        <a href="{PUBLIC_APP_URL}/chat/" style="background:white;color:#FF4C1F;padding:14px 36px;border-radius:100px;font-weight:700;text-decoration:none;display:inline-block;">{t('cta_button')}</a>
      </div>
      <p style="text-align:center;color:#8A8A8A;font-size:12px;margin-top:24px;">* {p.get('honest_note','')}</p>
    </div>
    """
    msg = MIMEMultipart('alternative')
    msg['Subject'] = t('subject')
    msg['From'] = GMAIL_USER
    msg['To'] = client_email
    msg.attach(MIMEText(html, 'html'))
    _send(msg, client_email)


_PAYMENT_CONFIRMATION_I18N = {
    "subtitle": {"he":"הבית לעסקים קטנים ובינוניים","en":"A home for small and medium businesses","fr":"Une maison pour les PME","ar":"بيت للشركات الصغيرة والمتوسطة","ru":"Дом для малого и среднего бизнеса"},
    "thanks": {"he":"תודה {name}! 🎉","en":"Thank you, {name}! 🎉","fr":"Merci, {name} ! 🎉","ar":"شكرًا {name}! 🎉","ru":"Спасибо, {name}! 🎉"},
    "thanks_body": {"he":"התשלום שלך התקבל בהצלחה ואנחנו כבר מתחילים לעבוד על התכנית שלך. בקרוב תקבל עדכון ראשון על ההתקדמות.","en":"Your payment was received successfully and we're already starting to work on your plan. You'll get a first progress update soon.","fr":"Votre paiement a bien été reçu et nous commençons déjà à travailler sur votre plan. Vous recevrez bientôt une première mise à jour.","ar":"تم استلام دفعتك بنجاح ونحن نبدأ العمل بالفعل على خطتك. ستصلك قريبًا أول تحديث على التقدّم.","ru":"Ваш платёж успешно получен, и мы уже начинаем работать над вашим планом. Скоро вы получите первое обновление о прогрессе."},
    "dashboard_title": {"he":"🔑 גישה לדשבורד האישי שלך","en":"🔑 Access to your personal dashboard","fr":"🔑 Accès à votre tableau de bord personnel","ar":"🔑 الوصول إلى لوحة التحكم الشخصية","ru":"🔑 Доступ к вашему личному кабинету"},
    "client_number": {"he":"מספר הלקוח שלך:","en":"Your client number:","fr":"Votre numéro client :","ar":"رقم العميل الخاص بك:","ru":"Ваш номер клиента:"},
    "dashboard_body": {"he":"תוכל לעקוב בזמן אמת אחרי הפעילות, החיבורים ופרטי המנוי שלך בדשבורד האישי. התחברות מהירה עם קוד חד-פעמי שיישלח לכתובת המייל הזו - בלי צורך בסיסמה:","en":"You can track your activity, connections, and subscription details in real time on your personal dashboard. Quick sign-in with a one-time code sent to this email address — no password needed:","fr":"Vous pouvez suivre en temps réel votre activité, vos connexions et les détails de votre abonnement sur votre tableau de bord personnel. Connexion rapide avec un code à usage unique envoyé à cette adresse e-mail — sans mot de passe :","ar":"يمكنك متابعة النشاط والاتصالات وتفاصيل الاشتراك في الوقت الفعلي عبر لوحة التحكم الشخصية. تسجيل دخول سريع برمز لمرة واحدة يُرسل إلى هذا البريد الإلكتروني - بلا حاجة لكلمة مرور:","ru":"Вы можете отслеживать активность, подключения и данные подписки в реальном времени в личном кабинете. Быстрый вход по одноразовому коду, отправленному на этот email — без пароля:"},
    "dashboard_btn": {"he":"כניסה לדשבורד שלי →","en":"Go to my dashboard →","fr":"Accéder à mon tableau de bord →","ar":"الدخول إلى لوحة التحكم ←","ru":"Перейти в личный кабинет →"},
    "whatsapp_note": {"he":"בינתיים, הצוות שלנו זמין בוואטסאפ לכל שאלה 💬","en":"In the meantime, our team is available on WhatsApp for any question 💬","fr":"En attendant, notre équipe est disponible sur WhatsApp pour toute question 💬","ar":"في هذه الأثناء، فريقنا متاح عبر واتساب لأي سؤال 💬","ru":"А пока наша команда доступна в WhatsApp по любым вопросам 💬"},
    "subject": {"he":"uallak — התשלום התקבל, מתחילים! 🚀","en":"uallak — Payment received, let's start! 🚀","fr":"uallak — Paiement reçu, c'est parti ! 🚀","ar":"uallak — تم استلام الدفع، لنبدأ! 🚀","ru":"uallak — Оплата получена, начинаем! 🚀"},
}


def send_payment_confirmation(client_email: str, client_name: str, client_id: int, language: str = None):
    lang = _lang_of(client_id, language)
    t = lambda key, **v: _tr(_PAYMENT_CONFIRMATION_I18N, key, lang, **v)
    html = f"""
    <div dir="{_LANG_DIR[lang]}" style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#F7F4EF;padding:32px;border-radius:16px;">
      <div style="text-align:center;margin-bottom:32px;">
        <h1 style="font-size:32px;font-weight:900;margin:0;">u<span style="color:#FF4C1F;">allak</span></h1>
        <p style="color:#8A8A8A;margin:4px 0 0;">{t('subtitle')}</p>
      </div>
      <div style="background:white;border-radius:12px;padding:28px;margin-bottom:20px;border-top:4px solid #FF4C1F;">
        <h2 style="margin:0 0 16px;color:#1A1A1A;">{t('thanks', name=client_name)}</h2>
        <p style="color:#3D3D3D;line-height:1.7;">{t('thanks_body')}</p>
      </div>
      <div style="background:#1A1A1A;border-radius:12px;padding:28px;margin-bottom:20px;color:white;">
        <h3 style="margin:0 0 12px;color:#FF4C1F;">{t('dashboard_title')}</h3>
        <p style="color:rgba(255,255,255,0.85);line-height:1.7;margin:0;">
          {t('client_number')} <strong style="color:#FFD166;">#{client_id}</strong>
        </p>
        <p style="color:rgba(255,255,255,0.75);line-height:1.7;margin:12px 0 0;">
          {t('dashboard_body')}
        </p>
        <a href="{PUBLIC_APP_URL}/login/" style="display:inline-block;margin-top:14px;background:#FF4C1F;color:white;padding:12px 28px;border-radius:100px;font-weight:700;text-decoration:none;">{t('dashboard_btn')}</a>
      </div>
      <div style="background:white;border-radius:12px;padding:24px;text-align:center;border:1.5px solid rgba(0,0,0,0.08);">
        <p style="color:#3D3D3D;margin:0;">{t('whatsapp_note')}</p>
      </div>
    </div>
    """
    msg = MIMEMultipart('alternative')
    msg['Subject'] = t('subject')
    msg['From'] = GMAIL_USER
    msg['To'] = client_email
    msg.attach(MIMEText(html, 'html'))
    _send(msg, client_email)


_LOGIN_CODE_I18N = {
    "subtitle": {"he":"הבית לעסקים קטנים ובינוניים","en":"A home for small and medium businesses","fr":"Une maison pour les PME","ar":"بيت للشركات الصغيرة والمتوسطة","ru":"Дом для малого и среднего бизнеса"},
    "greeting": {"he":"שלום{name_suffix} 👋","en":"Hello{name_suffix} 👋","fr":"Bonjour{name_suffix} 👋","ar":"مرحبًا{name_suffix} 👋","ru":"Здравствуйте{name_suffix} 👋"},
    "code_intro": {"he":"קוד ההתחברות שלך לדשבורד:","en":"Your dashboard sign-in code:","fr":"Votre code de connexion au tableau de bord :","ar":"رمز الدخول الخاص بك إلى لوحة التحكم:","ru":"Ваш код входа в личный кабинет:"},
    "expiry_note": {"he":"הקוד בתוקף ל-10 דקות. אם לא ביקשת קוד התחברות, אפשר להתעלם מהמייל הזה.","en":"The code is valid for 10 minutes. If you didn't request a sign-in code, you can ignore this email.","fr":"Le code est valable 10 minutes. Si vous n'avez pas demandé de code de connexion, vous pouvez ignorer cet e-mail.","ar":"الرمز صالح لمدة 10 دقائق. إذا لم تطلب رمز دخول، يمكنك تجاهل هذا البريد الإلكتروني.","ru":"Код действителен 10 минут. Если вы не запрашивали код входа, просто проигнорируйте это письмо."},
    "subject": {"he":"{code} — קוד ההתחברות שלך ל-uallak","en":"{code} — your uallak sign-in code","fr":"{code} — votre code de connexion uallak","ar":"{code} — رمز الدخول الخاص بك إلى uallak","ru":"{code} — ваш код входа в uallak"},
}


def send_login_code(client_email: str, client_name: str, code: str, client_id: int = None, language: str = None):
    lang = _lang_of(client_id, language)
    t = lambda key, **v: _tr(_LOGIN_CODE_I18N, key, lang, **v)
    name_suffix = f" {client_name}" if client_name else ""
    html = f"""
    <div dir="{_LANG_DIR[lang]}" style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#F7F4EF;padding:32px;border-radius:16px;">
      <div style="text-align:center;margin-bottom:32px;">
        <h1 style="font-size:32px;font-weight:900;margin:0;">u<span style="color:#FF4C1F;">allak</span></h1>
        <p style="color:#8A8A8A;margin:4px 0 0;">{t('subtitle')}</p>
      </div>
      <div style="background:white;border-radius:12px;padding:28px;margin-bottom:20px;border-top:4px solid #FF4C1F;text-align:center;">
        <h2 style="margin:0 0 16px;color:#1A1A1A;">{t('greeting', name_suffix=name_suffix)}</h2>
        <p style="color:#3D3D3D;line-height:1.7;margin:0 0 20px;">{t('code_intro')}</p>
        <div style="background:#1A1A1A;border-radius:12px;padding:20px;margin:0 0 20px;">
          <span style="color:#FF4C1F;font-size:36px;font-weight:900;letter-spacing:8px;">{code}</span>
        </div>
        <p style="color:#8A8A8A;line-height:1.6;font-size:13px;margin:0;">
          {t('expiry_note')}
        </p>
      </div>
    </div>
    """
    msg = MIMEMultipart('alternative')
    msg['Subject'] = t('subject', code=code)
    msg['From'] = GMAIL_USER
    msg['To'] = client_email
    msg.attach(MIMEText(html, 'html'))
    _send(msg, client_email)


def send_admin_alert(answers: dict, proposal: dict):
    p = proposal
    packages = p.get('packages', [])
    packages_html = ''.join([
        f"<p>📦 <strong>{pkg.get('name','')}:</strong> הקמה ₪{pkg.get('setup_fee_total',0)} + ניהול ₪{pkg.get('monthly_management_total',0)}/חודש</p>"
        for pkg in packages
    ]) or "<p>אין חבילות (לא אושר)</p>"
    html = f"""
    <div style="font-family:Arial,sans-serif;padding:20px;">
      <h2>🔔 ליד חדש — uallak</h2>
      <p><strong>עסק:</strong> {answers.get('intro','')[:100]}...</p>
      <p><strong>תקציב:</strong> {answers.get('marketing_budget','')}</p>
      <p><strong>מצב פיננסי:</strong> {answers.get('financial_status','')}</p>
      <p><strong>מטרה:</strong> {answers.get('main_goal','')}</p>
      <hr>
      {packages_html}
      <p><strong>רמת סיכון:</strong> {p.get('risk_level','')}</p>
      <p><strong>מאושר:</strong> {'✅ כן' if p.get('approved') else '❌ לא'}</p>
    </div>
    """
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"🔔 ליד חדש uallak — {answers.get('marketing_budget','')}"
    msg['From'] = GMAIL_USER
    msg['To'] = ADMIN_EMAIL
    msg.attach(MIMEText(html, 'html'))
    _send(msg, ADMIN_EMAIL)

_SALES_ALERT_I18N = {
    "subtitle": {"he":"הבית לעסקים קטנים ובינוניים","en":"A home for small and medium businesses","fr":"Une maison pour les PME","ar":"بيت للشركات الصغيرة والمتوسطة","ru":"Дом для малого и среднего бизнеса"},
    "greeting": {"he":"🎉 {name}, יש תוצאות!","en":"🎉 {name}, we have results!","fr":"🎉 {name}, il y a des résultats !","ar":"🎉 {name}، هناك نتائج!","ru":"🎉 {name}, есть результаты!"},
    "body": {"he":"הקמפיינים שלך הביאו אתמול <strong style=\"color:#00C96E;\">{total}</strong> המרות (לידים/פניות/רכישות):","en":"Your campaigns brought in <strong style=\"color:#00C96E;\">{total}</strong> conversions yesterday (leads/inquiries/purchases):","fr":"Vos campagnes ont généré hier <strong style=\"color:#00C96E;\">{total}</strong> conversions (leads/demandes/achats) :","ar":"جلبت حملاتك أمس <strong style=\"color:#00C96E;\">{total}</strong> تحويلات (عملاء محتملون/استفسارات/عمليات شراء):","ru":"Ваши кампании принесли вчера <strong style=\"color:#00C96E;\">{total}</strong> конверсий (лиды/обращения/покупки):"},
    "footer_note": {"he":"המספרים לפי דיווח פלטפורמות הפרסום — פירוט מלא בדשבורד שלך.","en":"Numbers per the ad platforms' own reporting — full details on your dashboard.","fr":"Chiffres selon les rapports des plateformes publicitaires — détails complets sur votre tableau de bord.","ar":"الأرقام وفق تقارير منصات الإعلانات نفسها — التفاصيل الكاملة في لوحة التحكم الخاصة بك.","ru":"Цифры по данным самих рекламных платформ — полные детали в вашем личном кабинете."},
    "dashboard_btn": {"he":"לדשבורד שלי →","en":"To my dashboard →","fr":"Vers mon tableau de bord →","ar":"إلى لوحة التحكم ←","ru":"В личный кабинет →"},
    "subject": {"he":"🎉 uallak — {total} המרות חדשות מהקמפיינים שלך!","en":"🎉 uallak — {total} new conversions from your campaigns!","fr":"🎉 uallak — {total} nouvelles conversions de vos campagnes !","ar":"🎉 uallak — {total} تحويلات جديدة من حملاتك!","ru":"🎉 uallak — {total} новых конверсий по вашим кампаниям!"},
}


def send_sales_alert(client_email: str, client_name: str, conversions: dict,
                      client_id: int = None, language: str = None):
    """Same-day celebration email when a client's campaigns produced
    conversions yesterday (engagement_agent's daily run) — distinct from the
    weekly report. `conversions` maps a Hebrew platform label to a count,
    e.g. {"גוגל": 3, "פייסבוק ואינסטגרם": 2} (platform labels stay Hebrew —
    they're internal keys from the ads agents, not client-facing prose)."""
    lang = _lang_of(client_id, language)
    t = lambda key, **v: _tr(_SALES_ALERT_I18N, key, lang, **v)
    total = round(sum(conversions.values()), 1)
    total_label = int(total) if total == int(total) else total
    lines_html = "".join([
        f'<p style="margin:6px 0;color:#3D3D3D;">✅ {label}: <strong>{int(v) if v == int(v) else v}</strong></p>'
        for label, v in conversions.items() if v
    ])
    html = f"""
    <div dir="{_LANG_DIR[lang]}" style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#F7F4EF;padding:32px;border-radius:16px;">
      <div style="text-align:center;margin-bottom:32px;">
        <h1 style="font-size:32px;font-weight:900;margin:0;">u<span style="color:#FF4C1F;">allak</span></h1>
        <p style="color:#8A8A8A;margin:4px 0 0;">{t('subtitle')}</p>
      </div>
      <div style="background:white;border-radius:12px;padding:28px;margin-bottom:20px;border-top:4px solid #00C96E;text-align:center;">
        <h2 style="margin:0 0 10px;color:#1A1A1A;">{t('greeting', name=client_name)}</h2>
        <p style="color:#3D3D3D;line-height:1.7;margin:0 0 16px;">
          {t('body', total=total_label)}
        </p>
        {lines_html}
        <p style="color:#8A8A8A;font-size:12px;margin:16px 0 0;">
          {t('footer_note')}
        </p>
      </div>
      <div style="text-align:center;">
        <a href="{PUBLIC_APP_URL}/login/" style="background:#FF4C1F;color:white;padding:12px 28px;border-radius:100px;font-weight:700;text-decoration:none;display:inline-block;">{t('dashboard_btn')}</a>
      </div>
    </div>
    """
    msg = MIMEMultipart('alternative')
    msg['Subject'] = t('subject', total=total_label)
    msg['From'] = GMAIL_USER
    msg['To'] = client_email
    msg.attach(MIMEText(html, 'html'))
    _send(msg, client_email)


def send_google_ads_weekly_report(report: dict):
    """Weekly Google Ads performance digest for the team (not clients)."""
    _send_weekly_platform_report(report, "Google Ads")

def send_meta_weekly_report(report: dict):
    """Weekly Meta (Facebook + Instagram, paid + organic) digest for the team."""
    _send_weekly_platform_report(report, "Meta")

def _send_weekly_platform_report(report: dict, platform_label: str):
    def _fmt_change(pct):
        if pct is None:
            return ""
        arrow = "▲" if pct > 0 else "▼"
        return f' <span style="color:{"#FF4C1F" if pct > 0 else "#00C96E"};font-size:12px;">({arrow}{abs(pct)}%)</span>'

    def _engagement_line(c):
        # Meta reports carry organic engagement next to the paid numbers -
        # Google reports never set this field, so the line simply doesn't render
        engagement = c.get("engagement") or {}
        parts = []
        fb = engagement.get("facebook")
        if fb:
            parts.append(f"פייסבוק: {fb.get('posts', 0)} פוסטים · {fb.get('likes', 0)} לייקים · "
                         f"{fb.get('comments', 0)} תגובות · {fb.get('shares', 0)} שיתופים")
        ig = engagement.get("instagram")
        if ig:
            parts.append(f"אינסטגרם: {ig.get('posts', 0)} פוסטים · {ig.get('likes', 0)} לייקים · "
                         f"{ig.get('comments', 0)} תגובות")
        if not parts:
            return ""
        return ('<p style="margin:0 0 14px;color:#3D3D3D;font-size:13px;">🌱 אורגני (7 ימים): '
                + " | ".join(parts) + '</p>')

    clients_html = ""
    for c in report.get("clients", []):
        last, prev = c["totals_last7"], c["totals_prev7"]
        rows = "".join([f"""
            <tr>
              <td style="padding:6px 10px;border-bottom:1px solid #eee;">{camp['name']} <span style="color:#8A8A8A;font-size:11px;">({camp['status']})</span></td>
              <td style="padding:6px 10px;border-bottom:1px solid #eee;text-align:center;">₪{camp['last7']['cost']}</td>
              <td style="padding:6px 10px;border-bottom:1px solid #eee;text-align:center;">{camp['last7']['clicks']}</td>
              <td style="padding:6px 10px;border-bottom:1px solid #eee;text-align:center;">{camp['last7']['impressions']}</td>
              <td style="padding:6px 10px;border-bottom:1px solid #eee;text-align:center;">{camp['last7']['conversions']}</td>
            </tr>""" for camp in c.get("campaigns", [])]) or '<tr><td colspan="5" style="padding:10px;color:#8A8A8A;">אין קמפיינים עם נתונים השבוע</td></tr>'
        clients_html += f"""
        <div style="background:white;border-radius:12px;padding:24px;margin-bottom:16px;">
          <h3 style="margin:0 0 4px;">{c['client_name']} <span style="color:#8A8A8A;font-size:13px;font-weight:400;">(חשבון {c['customer_id']})</span></h3>
          <p style="margin:0 0 14px;color:#3D3D3D;">
            הוצאה שבועית: <strong>₪{last['cost']}</strong>{_fmt_change(c.get('cost_change_pct'))}
            · קליקים: <strong>{last['clicks']}</strong> (שבוע קודם: {prev['clicks']})
            · המרות: <strong>{last['conversions']}</strong> (שבוע קודם: {prev['conversions']})
          </p>
          {_engagement_line(c)}
          <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <tr style="background:#F7F4EF;">
              <th style="padding:6px 10px;text-align:right;">קמפיין</th>
              <th style="padding:6px 10px;">עלות</th>
              <th style="padding:6px 10px;">קליקים</th>
              <th style="padding:6px 10px;">חשיפות</th>
              <th style="padding:6px 10px;">המרות</th>
            </tr>
            {rows}
          </table>
        </div>"""

    if not clients_html:
        clients_html = f'<div style="background:white;border-radius:12px;padding:24px;"><p style="margin:0;color:#8A8A8A;">אין עדיין חשבונות {platform_label} מחוברים.</p></div>'

    def _bullets(items):
        return "".join([f'<p style="margin:6px 0;">• {item}</p>' for item in items])

    insights_html = ""
    if report.get("highlights") or report.get("recommendations"):
        insights_html = f"""
        <div style="background:#1A1A1A;border-radius:12px;padding:24px;margin-bottom:16px;color:white;">
          {f'<h3 style="margin:0 0 10px;color:#FFD166;">✨ עיקרי השבוע</h3>{_bullets(report.get("highlights", []))}' if report.get("highlights") else ''}
          {f'<h3 style="margin:16px 0 10px;color:#FF4C1F;">🎯 המלצות</h3>{_bullets(report.get("recommendations", []))}' if report.get("recommendations") else ''}
        </div>"""

    html = f"""
    <div dir="rtl" style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto;background:#F7F4EF;padding:32px;border-radius:16px;">
      <div style="text-align:center;margin-bottom:24px;">
        <h1 style="font-size:28px;font-weight:900;margin:0;">u<span style="color:#FF4C1F;">allak</span></h1>
        <p style="color:#8A8A8A;margin:4px 0 0;">דוח {platform_label} שבועי — 7 ימים אחרונים מול השבוע שקדם</p>
      </div>
      {insights_html}
      {clients_html}
    </div>
    """
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"📊 uallak — דוח {platform_label} שבועי"
    msg['From'] = GMAIL_USER
    msg['To'] = ADMIN_EMAIL
    msg.attach(MIMEText(html, 'html'))
    _send(msg, ADMIN_EMAIL)


_OFFBOARDING_SHELL_I18N = {
    "subtitle": {"he":"הבית לעסקים קטנים ובינוניים","en":"A home for small and medium businesses","fr":"Une maison pour les PME","ar":"بيت للشركات الصغيرة والمتوسطة","ru":"Дом для малого и среднего бизнеса"},
    "questions_note": {"he":"שאלות על סיום ההתקשרות? אפשר פשוט להשיב למייל הזה 💬","en":"Questions about ending the relationship? Just reply to this email 💬","fr":"Des questions sur la fin de la collaboration ? Répondez simplement à cet e-mail 💬","ar":"أسئلة حول إنهاء التعاون؟ يمكنك ببساطة الرد على هذا البريد الإلكتروني 💬","ru":"Вопросы о завершении сотрудничества? Просто ответьте на это письмо 💬"},
}


def _offboarding_email(client_email: str, client_name: str, title: str,
                        body_lines: list, subject: str, lang: str,
                        attachment_name: str = "", attachment_content: str = ""):
    """Shared branded shell for the closure/transfer confirmations - the one
    email a leaving client must actually receive, because it's their written
    proof that billing stopped. The data export rides along as an attachment:
    offboarded clients are hard-locked out of the dashboard, so this email is
    their only self-service way to get their data. title/body_lines arrive
    pre-translated from the caller (send_account_closed/_transferred); this
    shell only localizes its own static chrome."""
    t = lambda key, **v: _tr(_OFFBOARDING_SHELL_I18N, key, lang, **v)
    paragraphs = "".join(
        f'<p style="color:#3D3D3D;line-height:1.7;margin:0 0 14px;">{line}</p>'
        for line in body_lines
    )
    html = f"""
    <div dir="{_LANG_DIR[lang]}" style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#F7F4EF;padding:32px;border-radius:16px;">
      <div style="text-align:center;margin-bottom:32px;">
        <h1 style="font-size:32px;font-weight:900;margin:0;">u<span style="color:#FF4C1F;">allak</span></h1>
        <p style="color:#8A8A8A;margin:4px 0 0;">{t('subtitle')}</p>
      </div>
      <div style="background:white;border-radius:12px;padding:28px;margin-bottom:20px;border-top:4px solid #FF4C1F;">
        <h2 style="margin:0 0 16px;color:#1A1A1A;">{title}</h2>
        {paragraphs}
      </div>
      <div style="background:white;border-radius:12px;padding:24px;text-align:center;border:1.5px solid rgba(0,0,0,0.08);">
        <p style="color:#3D3D3D;margin:0;">{t('questions_note')}</p>
      </div>
    </div>
    """
    # 'mixed' (not 'alternative') so a real file attachment renders as one
    msg = MIMEMultipart('mixed')
    msg['Subject'] = subject
    msg['From'] = GMAIL_USER
    msg['To'] = client_email
    msg.attach(MIMEText(html, 'html'))
    if attachment_name and attachment_content:
        part = MIMEApplication(attachment_content.encode('utf-8'), _subtype='json')
        part.add_header('Content-Disposition', 'attachment', filename=attachment_name)
        msg.attach(part)
    _send(msg, client_email)


_ACCOUNT_CLOSED_I18N = {
    "title": {"he":"להתראות{name_suffix}, ותודה על הכל 🙏","en":"Goodbye{name_suffix}, and thank you for everything 🙏","fr":"Au revoir{name_suffix}, et merci pour tout 🙏","ar":"إلى اللقاء{name_suffix}، وشكرًا على كل شيء 🙏","ru":"До свидания{name_suffix}, и спасибо за всё 🙏"},
    "p1": {"he":"בקשתך לסגירת החשבון בוצעה.","en":"Your request to close the account has been processed.","fr":"Votre demande de clôture de compte a été traitée.","ar":"تم تنفيذ طلبك بإغلاق الحساب.","ru":"Ваш запрос на закрытие аккаунта выполнен."},
    "p2": {"he":"<strong>המנוי ב-PayPal בוטל</strong> — לא יהיו יותר חיובים מאיתנו. אם קיים חיוב שכבר נקלט לפני הביטול, הוא האחרון.","en":"<strong>Your PayPal subscription has been cancelled</strong> — there will be no more charges from us. If a charge was already processed before the cancellation, it is the last one.","fr":"<strong>Votre abonnement PayPal a été annulé</strong> — il n'y aura plus de prélèvements de notre part. Si un paiement a déjà été traité avant l'annulation, il s'agit du dernier.","ar":"<strong>تم إلغاء اشتراك PayPal</strong> — لن تكون هناك رسوم إضافية منا. إذا كانت هناك رسوم قد تمت معالجتها قبل الإلغاء، فهي الأخيرة.","ru":"<strong>Подписка в PayPal отменена</strong> — больше списаний с нашей стороны не будет. Если списание уже прошло до отмены, оно было последним."},
    "p3": {"he":"כל חיבורי המערכות (Google Ads, Meta, האתר) נותקו והפרטים שנשמרו אצלנו לצורך החיבור נמחקו. החשבונות עצמם שלך והם ממשיכים להתקיים כרגיל.","en":"All system connections (Google Ads, Meta, the website) have been disconnected and the details we held for the connection have been deleted. The accounts themselves are yours and continue to exist as usual.","fr":"Toutes les connexions aux systèmes (Google Ads, Meta, le site web) ont été déconnectées et les informations que nous détenions pour la connexion ont été supprimées. Les comptes eux-mêmes vous appartiennent et continuent d'exister normalement.","ar":"تم فصل جميع اتصالات الأنظمة (Google Ads وMeta والموقع الإلكتروني) وحُذفت التفاصيل التي احتفظنا بها لغرض الربط. الحسابات نفسها ملكك وتستمر بالوجود كالمعتاد.","ru":"Все подключения к системам (Google Ads, Meta, сайт) отключены, а данные, которые мы хранили для подключения, удалены. Сами аккаунты принадлежат вам и продолжают существовать в обычном режиме."},
    "p4": {"he":"מצורף למייל הזה <strong>עותק מלא של הנתונים שלך</strong> (קובץ JSON) — היסטוריית פעילות, חיובים ונתוני קמפיינים. עותק ארכיוני נשמר אצלנו לצרכי תיעוד חשבונאי, והגישה לדשבורד נסגרת עם החשבון.","en":"Attached to this email is a <strong>full copy of your data</strong> (a JSON file) — activity history, billing, and campaign data. An archival copy is kept by us for accounting record purposes, and dashboard access closes along with the account.","fr":"Une <strong>copie complète de vos données</strong> (fichier JSON) est jointe à cet e-mail — historique d'activité, facturation et données de campagne. Une copie d'archive est conservée par nos soins à des fins comptables, et l'accès au tableau de bord est clôturé avec le compte.","ar":"مرفق بهذا البريد الإلكتروني <strong>نسخة كاملة من بياناتك</strong> (ملف JSON) — سجل النشاط والفواتير وبيانات الحملات. نحتفظ بنسخة أرشيفية لأغراض التوثيق المحاسبي، ويُغلق الوصول إلى لوحة التحكم مع إغلاق الحساب.","ru":"К этому письму приложена <strong>полная копия ваших данных</strong> (файл JSON) — история активности, платежи и данные кампаний. Архивная копия хранится у нас для бухгалтерского учёта, а доступ к личному кабинету закрывается вместе с аккаунтом."},
    "p5": {"he":"אם תרצו לחזור מתישהו — נשמח לקבל אתכם שוב.","en":"If you'd like to come back at some point — we'd be happy to have you again.","fr":"Si vous souhaitez revenir un jour, nous serons ravis de vous accueillir à nouveau.","ar":"إذا رغبت بالعودة في أي وقت — يسعدنا استقبالك مجددًا.","ru":"Если захотите вернуться когда-нибудь — будем рады видеть вас снова."},
    "subject": {"he":"uallak — החשבון נסגר והמנוי בוטל ✔","en":"uallak — Account closed and subscription cancelled ✔","fr":"uallak — Compte clôturé et abonnement annulé ✔","ar":"uallak — تم إغلاق الحساب وإلغاء الاشتراك ✔","ru":"uallak — Аккаунт закрыт, подписка отменена ✔"},
}


def send_account_closed(client_email: str, client_name: str,
                         export_name: str = "", export_content: str = "",
                         client_id: int = None, language: str = None):
    lang = _lang_of(client_id, language)
    t = lambda key, **v: _tr(_ACCOUNT_CLOSED_I18N, key, lang, **v)
    name_suffix = f" {client_name}" if client_name else ""
    _offboarding_email(
        client_email, client_name,
        title=t('title', name_suffix=name_suffix),
        body_lines=[t('p1'), t('p2'), t('p3'), t('p4'), t('p5')],
        subject=t('subject'), lang=lang,
        attachment_name=export_name, attachment_content=export_content,
    )


_ACCOUNT_TRANSFERRED_I18N = {
    "title": {"he":"בהצלחה בהמשך הדרך{name_suffix} 🤝","en":"Best of luck going forward{name_suffix} 🤝","fr":"Bonne continuation{name_suffix} 🤝","ar":"بالتوفيق في المرحلة القادمة{name_suffix} 🤝","ru":"Удачи в дальнейшем{name_suffix} 🤝"},
    "p1": {"he":"בקשתך לסיום הניהול אצלנו לקראת מעבר לגורם מנהל אחר בוצעה.","en":"Your request to end our management ahead of moving to another provider has been processed.","fr":"Votre demande de fin de gestion chez nous en vue d'un transfert vers un autre prestataire a été traitée.","ar":"تم تنفيذ طلبك بإنهاء الإدارة لدينا تمهيدًا للانتقال إلى جهة إدارة أخرى.","ru":"Ваш запрос на завершение нашего ведения в связи с переходом к другому исполнителю выполнен."},
    "p2": {"he":"<strong>המנוי ב-PayPal בוטל</strong> — לא יהיו יותר חיובים מאיתנו.","en":"<strong>Your PayPal subscription has been cancelled</strong> — there will be no more charges from us.","fr":"<strong>Votre abonnement PayPal a été annulé</strong> — il n'y aura plus de prélèvements de notre part.","ar":"<strong>تم إلغاء اشتراك PayPal</strong> — لن تكون هناك رسوم إضافية منا.","ru":"<strong>Подписка в PayPal отменена</strong> — больше списаний с нашей стороны не будет."},
    "p3": {"he":"ניתקנו את הגישה שלנו לחשבונות הפרסום ולאתר — <strong>החשבונות עצמם לא נפגעו</strong>: הקמפיינים, העמודים והאתר נשארים בדיוק כפי שהם, בבעלותך המלאה, ומי שינהל אותם מעכשיו פשוט יקבל גישה ישירות ממך.","en":"We've disconnected our access to your advertising accounts and website — <strong>the accounts themselves are unaffected</strong>: the campaigns, pages, and website remain exactly as they are, fully owned by you, and whoever manages them from now on simply gets access directly from you.","fr":"Nous avons révoqué notre accès à vos comptes publicitaires et à votre site web — <strong>les comptes eux-mêmes ne sont pas affectés</strong> : les campagnes, pages et site web restent exactement tels quels, en votre pleine propriété, et quiconque les gérera désormais obtiendra simplement l'accès directement de vous.","ar":"قمنا بفصل وصولنا إلى حسابات الإعلانات والموقع الإلكتروني — <strong>الحسابات نفسها لم تتأثر</strong>: تبقى الحملات والصفحات والموقع كما هي تمامًا، بملكيتك الكاملة، ومن سيديرها من الآن فصاعدًا سيحصل على الوصول مباشرة منك.","ru":"Мы отключили наш доступ к рекламным аккаунтам и сайту — <strong>сами аккаунты не пострадали</strong>: кампании, страницы и сайт остаются в точности такими же, полностью в вашей собственности, а тот, кто будет управлять ими впредь, просто получит доступ напрямую от вас."},
    "p4": {"he":"מצורף למייל הזה <strong>קובץ סיכום הנתונים המלא</strong> (JSON) — היסטוריית פעילות ונתוני הקמפיינים האחרונים. מומלץ להעביר אותו לגורם המנהל החדש. שימו לב: הגישה לדשבורד מסתיימת עם המעבר, אז הקובץ הזה הוא העותק שלך.","en":"Attached to this email is a <strong>full data summary file</strong> (JSON) — activity history and recent campaign data. We recommend passing it on to the new managing party. Note: dashboard access ends with the transfer, so this file is your copy.","fr":"Un <strong>fichier de synthèse complet des données</strong> (JSON) est joint à cet e-mail — historique d'activité et données récentes de campagne. Nous vous recommandons de le transmettre au nouveau prestataire. Notez que l'accès au tableau de bord prend fin avec le transfert, ce fichier est donc votre copie.","ar":"مرفق بهذا البريد الإلكتروني <strong>ملف ملخص كامل للبيانات</strong> (JSON) — سجل النشاط وبيانات الحملات الأخيرة. يُنصح بتمرير الملف إلى الجهة المديرة الجديدة. ملاحظة: ينتهي الوصول إلى لوحة التحكم مع الانتقال، لذا هذا الملف هو نسختك.","ru":"К этому письму приложён <strong>полный файл сводки данных</strong> (JSON) — история активности и данные недавних кампаний. Рекомендуем передать его новому исполнителю. Обратите внимание: доступ к личному кабинету прекращается с переходом, так что этот файл — ваша копия."},
    "p5": {"he":"תודה על התקופה המשותפת — והדלת תמיד פתוחה.","en":"Thank you for the time we worked together — and the door is always open.","fr":"Merci pour cette période passée ensemble — et la porte reste toujours ouverte.","ar":"شكرًا على الفترة التي عملنا فيها معًا — والباب مفتوح دائمًا.","ru":"Спасибо за время, проведённое вместе — и дверь всегда открыта."},
    "subject": {"he":"uallak — סיום ניהול ומעבר בוצעו, המנוי בוטל ✔","en":"uallak — Management ended and transfer complete, subscription cancelled ✔","fr":"uallak — Fin de gestion et transfert effectués, abonnement annulé ✔","ar":"uallak — تم إنهاء الإدارة والانتقال، وإلغاء الاشتراك ✔","ru":"uallak — Ведение завершено, переход выполнен, подписка отменена ✔"},
}


def send_account_transferred(client_email: str, client_name: str,
                              export_name: str = "", export_content: str = "",
                              client_id: int = None, language: str = None):
    lang = _lang_of(client_id, language)
    t = lambda key, **v: _tr(_ACCOUNT_TRANSFERRED_I18N, key, lang, **v)
    name_suffix = f", {client_name}" if client_name else ""
    _offboarding_email(
        client_email, client_name,
        title=t('title', name_suffix=name_suffix),
        body_lines=[t('p1'), t('p2'), t('p3'), t('p4'), t('p5')],
        subject=t('subject'), lang=lang,
        attachment_name=export_name, attachment_content=export_content,
    )


def _send(msg, to_email):
    if not GMAIL_APP_PASSWORD:
        print(f"❌ GMAIL_APP_PASSWORD not set — email to {to_email} NOT sent (set it in the Cloud Run env vars)")
        return
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, to_email, msg.as_string())
        print(f"✅ מייל נשלח ל-{to_email}")
    except Exception as e:
        print(f"❌ שגיאה בשליחת מייל: {e}")
