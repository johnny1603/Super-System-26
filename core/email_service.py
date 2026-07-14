import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# The app password previously hardcoded here leaked into git history — it must be
# rotated in the Google account and supplied via the GMAIL_APP_PASSWORD env var.
GMAIL_USER = os.environ.get("GMAIL_USER", "johnny_support@uallak.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", GMAIL_USER)
PUBLIC_APP_URL = os.environ.get("PUBLIC_APP_URL", "https://uallak.com")

def send_client_report(client_email: str, client_name: str, proposal: dict):
    p = proposal
    packages = p.get('packages', [])
    packages_html = ''.join([f"""
        <div style="background:#252525;border-radius:10px;padding:20px;margin-bottom:12px;">
          <h4 style="margin:0 0 6px;color:#FFD166;">{pkg.get('name','')}</h4>
          <p style="color:rgba(255,255,255,0.75);margin:0 0 10px;font-size:14px;">{pkg.get('description','')}</p>
          <p style="margin:0;">עלות הקמה: <strong style="color:#FFD166;">₪{pkg.get('setup_fee_total',0)}</strong> · דמי ניהול חודשיים: <strong style="color:#FFD166;">₪{pkg.get('monthly_management_total',0)}</strong></p>
          <p style="color:#00C96E;margin:6px 0 0;">🎁 הטבה: 2 חודשי ניהול חינם — שווי ₪{pkg.get('benefit_value',0)}</p>
        </div>
    """ for pkg in packages])
    scarcity_html = f'<p style="text-align:center;color:#FF4C1F;font-weight:700;margin:0 0 20px;">🔥 {p.get("scarcity_note")}</p>' if p.get('scarcity_note') else ''

    html = f"""
    <div dir="rtl" style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#F7F4EF;padding:32px;border-radius:16px;">
      <div style="text-align:center;margin-bottom:32px;">
        <h1 style="font-size:32px;font-weight:900;margin:0;">u<span style="color:#FF4C1F;">allak</span></h1>
        <p style="color:#8A8A8A;margin:4px 0 0;">הבית לעסקים קטנים ובינוניים</p>
      </div>
      <div style="background:white;border-radius:12px;padding:28px;margin-bottom:20px;">
        <h2 style="margin:0 0 16px;">שלום {client_name} 👋</h2>
        <p style="color:#3D3D3D;line-height:1.7;">המערכת סיימה לנתח את העסק שלך ובנתה תכנית עבודה מותאמת אישית.</p>
      </div>
      <div style="background:white;border-radius:12px;padding:28px;margin-bottom:20px;">
        <h3 style="color:#FF4C1F;margin:0 0 16px;">📋 תמונת מצב</h3>
        <p style="color:#3D3D3D;line-height:1.7;">{p.get('business_summary','')}</p>
      </div>
      {f'''<div style="background:white;border-radius:12px;padding:28px;margin-bottom:20px;">
        <h3 style="color:#FF4C1F;margin:0 0 16px;">📊 תמונת השוק שלך</h3>
        <p style="color:#3D3D3D;line-height:1.7;">{p.get('market_reality','')}</p>
      </div>''' if p.get('market_reality') else ''}
      <div style="background:white;border-radius:12px;padding:28px;margin-bottom:20px;">
        <h3 style="color:#FF4C1F;margin:0 0 16px;">🎯 היעדים שלנו ל-90 יום</h3>
        <p style="color:#8A8A8A;font-size:12px;margin:0 0 10px;">הערכות מבוססות ניסיון ונתוני שוק — לא הבטחות מדויקות</p>
        {''.join([f'<p style="margin:8px 0;">✅ {g}</p>' for g in p.get('goals_90_days',[])])}
      </div>
      <div style="background:#1A1A1A;border-radius:12px;padding:28px;margin-bottom:20px;color:white;">
        <h3 style="margin:0 0 16px;">💰 המסלולים שלך לבחירה</h3>
        {packages_html}
      </div>
      {scarcity_html}
      <div style="background:#FF4C1F;border-radius:12px;padding:28px;text-align:center;">
        <h3 style="color:white;margin:0 0 16px;">מוכן להתחיל?</h3>
        <a href="{PUBLIC_APP_URL}/chat/" style="background:white;color:#FF4C1F;padding:14px 36px;border-radius:100px;font-weight:700;text-decoration:none;display:inline-block;">התחל עכשיו →</a>
      </div>
      <p style="text-align:center;color:#8A8A8A;font-size:12px;margin-top:24px;">* {p.get('honest_note','')}</p>
    </div>
    """
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"uallak — התכנית שלך מוכנה! 🚀"
    msg['From'] = GMAIL_USER
    msg['To'] = client_email
    msg.attach(MIMEText(html, 'html'))
    _send(msg, client_email)


def send_payment_confirmation(client_email: str, client_name: str, client_id: int):
    html = f"""
    <div dir="rtl" style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#F7F4EF;padding:32px;border-radius:16px;">
      <div style="text-align:center;margin-bottom:32px;">
        <h1 style="font-size:32px;font-weight:900;margin:0;">u<span style="color:#FF4C1F;">allak</span></h1>
        <p style="color:#8A8A8A;margin:4px 0 0;">הבית לעסקים קטנים ובינוניים</p>
      </div>
      <div style="background:white;border-radius:12px;padding:28px;margin-bottom:20px;border-top:4px solid #FF4C1F;">
        <h2 style="margin:0 0 16px;color:#1A1A1A;">תודה {client_name}! 🎉</h2>
        <p style="color:#3D3D3D;line-height:1.7;">התשלום שלך התקבל בהצלחה ואנחנו כבר מתחילים לעבוד על התכנית שלך. בקרוב תקבל עדכון ראשון על ההתקדמות.</p>
      </div>
      <div style="background:#1A1A1A;border-radius:12px;padding:28px;margin-bottom:20px;color:white;">
        <h3 style="margin:0 0 12px;color:#FF4C1F;">🔑 גישה לדשבורד האישי שלך</h3>
        <p style="color:rgba(255,255,255,0.85);line-height:1.7;margin:0;">
          מספר הלקוח שלך: <strong style="color:#FFD166;">#{client_id}</strong>
        </p>
        <p style="color:rgba(255,255,255,0.75);line-height:1.7;margin:12px 0 0;">
          תוכל לעקוב בזמן אמת אחרי הפעילות, החיבורים ופרטי המנוי שלך בדשבורד האישי. התחברות מהירה
          עם קוד חד-פעמי שיישלח לכתובת המייל הזו - בלי צורך בסיסמה:
        </p>
        <a href="{PUBLIC_APP_URL}/login/" style="display:inline-block;margin-top:14px;background:#FF4C1F;color:white;padding:12px 28px;border-radius:100px;font-weight:700;text-decoration:none;">כניסה לדשבורד שלי →</a>
      </div>
      <div style="background:white;border-radius:12px;padding:24px;text-align:center;border:1.5px solid rgba(0,0,0,0.08);">
        <p style="color:#3D3D3D;margin:0;">בינתיים, הצוות שלנו זמין בוואטסאפ לכל שאלה 💬</p>
      </div>
    </div>
    """
    msg = MIMEMultipart('alternative')
    msg['Subject'] = "uallak — התשלום התקבל, מתחילים! 🚀"
    msg['From'] = GMAIL_USER
    msg['To'] = client_email
    msg.attach(MIMEText(html, 'html'))
    _send(msg, client_email)


def send_login_code(client_email: str, client_name: str, code: str):
    html = f"""
    <div dir="rtl" style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;background:#F7F4EF;padding:32px;border-radius:16px;">
      <div style="text-align:center;margin-bottom:32px;">
        <h1 style="font-size:32px;font-weight:900;margin:0;">u<span style="color:#FF4C1F;">allak</span></h1>
        <p style="color:#8A8A8A;margin:4px 0 0;">הבית לעסקים קטנים ובינוניים</p>
      </div>
      <div style="background:white;border-radius:12px;padding:28px;margin-bottom:20px;border-top:4px solid #FF4C1F;text-align:center;">
        <h2 style="margin:0 0 16px;color:#1A1A1A;">שלום{f' {client_name}' if client_name else ''} 👋</h2>
        <p style="color:#3D3D3D;line-height:1.7;margin:0 0 20px;">קוד ההתחברות שלך לדשבורד:</p>
        <div style="background:#1A1A1A;border-radius:12px;padding:20px;margin:0 0 20px;">
          <span style="color:#FF4C1F;font-size:36px;font-weight:900;letter-spacing:8px;">{code}</span>
        </div>
        <p style="color:#8A8A8A;line-height:1.6;font-size:13px;margin:0;">
          הקוד בתוקף ל-10 דקות. אם לא ביקשת קוד התחברות, אפשר להתעלם מהמייל הזה.
        </p>
      </div>
    </div>
    """
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"{code} — קוד ההתחברות שלך ל-uallak"
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
