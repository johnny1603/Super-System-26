import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

GMAIL_USER = "johnny_support@uallak.com"
GMAIL_APP_PASSWORD = "gabg fbdq yxil uumt"
ADMIN_EMAIL = "johnny_support@uallak.com"

def send_client_report(client_email: str, client_name: str, proposal: dict):
    p = proposal
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
      <div style="background:white;border-radius:12px;padding:28px;margin-bottom:20px;">
        <h3 style="color:#FF4C1F;margin:0 0 16px;">🎯 היעדים שלנו ל-90 יום</h3>
        {''.join([f'<p style="margin:8px 0;">✅ {g}</p>' for g in p.get('goals_90_days',[])])}
      </div>
      <div style="background:#1A1A1A;border-radius:12px;padding:28px;margin-bottom:20px;color:white;">
        <h3 style="margin:0 0 16px;">💰 הצעת המחיר שלך</h3>
        <p>עלות הקמה חד-פעמית: <strong style="color:#FFD166;">₪{p.get('setup_fee_total',0)}</strong></p>
        <p>דמי ניהול חודשיים: <strong style="color:#FFD166;">₪{p.get('monthly_management_total',0)}</strong></p>
        <p style="color:#00C96E;">🎁 הטבה: 2 חודשי ניהול חינם — שווי ₪{p.get('benefit_value',0)}</p>
      </div>
      <div style="background:#FF4C1F;border-radius:12px;padding:28px;text-align:center;">
        <h3 style="color:white;margin:0 0 16px;">מוכן להתחיל?</h3>
        <a href="https://uallak.com/payment" style="background:white;color:#FF4C1F;padding:14px 36px;border-radius:100px;font-weight:700;text-decoration:none;display:inline-block;">התחל עכשיו →</a>
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

def send_admin_alert(answers: dict, proposal: dict):
    p = proposal
    html = f"""
    <div style="font-family:Arial,sans-serif;padding:20px;">
      <h2>🔔 ליד חדש — uallak</h2>
      <p><strong>עסק:</strong> {answers.get('intro','')[:100]}...</p>
      <p><strong>תקציב:</strong> {answers.get('marketing_budget','')}</p>
      <p><strong>מצב פיננסי:</strong> {answers.get('financial_status','')}</p>
      <p><strong>מטרה:</strong> {answers.get('main_goal','')}</p>
      <hr>
      <p><strong>הצעת מחיר:</strong> הקמה ₪{p.get('setup_fee_total',0)} + ניהול ₪{p.get('monthly_management_total',0)}/חודש</p>
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

def _send(msg, to_email):
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, to_email, msg.as_string())
        print(f"✅ מייל נשלח ל-{to_email}")
    except Exception as e:
        print(f"❌ שגיאה בשליחת מייל: {e}")
