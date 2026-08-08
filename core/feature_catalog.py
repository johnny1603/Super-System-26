"""THE list of what uallak offers a client today — one entry per capability.

## Why this exists

Two places needed the same knowledge and neither had it:

1. `agents/interview_agent.py` tells the LLM to "answer whatever they ask about
   the platform" while handing it **no facts about the platform**. It could only
   answer from generic knowledge or invent. Now it renders this catalogue.
2. `agents/engagement_agent.run_feature_announcement` has to decide WHICH
   clients a new feature is relevant to. That is the same question this
   catalogue already answers per entry.

A second copy of "what the product does" would drift from the first within a
release, which is exactly how the interview went stale. So: one list, two
readers.

## Adding a feature — this is the whole job

Append one dict to `FEATURES`. Nothing else changes: the interview starts
explaining it, announcements can target it, and the admin announcement form
lists it in its dropdown. If you ship something client-facing and do NOT add it
here, the interview will keep describing a product that no longer matches.

## Fields

- `key`          stable slug. Announcements reference it; never rename one in use.
- `name_he`      what the client would call it.
- `what_he`      one sentence, client-facing, in the client's terms.
- `where_he`     where they find it. "" for things with no screen of their own.
- `access`       how the client gets it:
                 `self_serve` — they can do it themselves right now
                 `manual`     — they have to talk to a human first (ManyChat/Make)
                 `automatic`  — we do it, nothing for them to press
- `persona`      which specialist owns the conversation about it. Must be a key
                 in `support_agent.PERSONAS`, or "general" for the concierge.
                 Announcements are delivered in this persona's voice and land in
                 that persona's chat thread.
- `relevance`    who it applies to. Exactly one of:
                 `{"always": True}`               — everyone
                 `{"services": [...]}`            — clients whose PURCHASED package
                                                    lists one of these in
                                                    `recommended_services`
                 `{"connections": [...]}`         — clients with one of these
                                                    platforms actually connected
                 `services` and `connections` may be combined (OR).
"""
from core.agent_base import log_step

SERVICE_NAME = "feature_catalog"

FEATURES = [
    # ── Always relevant: the dashboard every client gets ──────────────────────
    {
        "key": "dashboard_home",
        "name_he": "מסך הבית",
        "what_he": "החבילה שלכם, דמי הניהול, מועד החיוב הבא, וכל מה שהמערכת עשתה עבורכם בזמן אמת.",
        "where_he": "לשונית ׳בית׳",
        "access": "automatic",
        "persona": "general",
        "relevance": {"always": True},
    },
    {
        "key": "approvals",
        "name_he": "ממתין לאישור שלך",
        "what_he": "כל חומר שהמערכת מכינה — פוסט, תמונה, סרטון, רעיון לקמפיין — מחכה לאישור שלכם לפני שהוא יוצא לאוויר. שום דבר לא מתפרסם בלי שאישרתם.",
        "where_he": "מסך הבית, מעל החיבורים",
        "access": "self_serve",
        "persona": "general",
        "relevance": {"always": True},
    },
    {
        "key": "connections",
        "name_he": "חיבורי חשבונות",
        "what_he": "חיבור החשבונות שהחבילה שלכם כוללת (גוגל, מטא, טיקטוק, יוטיוב, האתר) — לחיצה אחת לכל כרטיס. אנחנו לא יכולים להתחיל לעבוד לפני שהם מחוברים.",
        "where_he": "מסך הבית, אזור ׳חיבורים׳",
        "access": "self_serve",
        "persona": "general",
        "relevance": {"always": True},
    },
    {
        "key": "team_chat",
        "name_he": "הצוות בצ׳אט",
        "what_he": "אפשר לדבר עם כל מומחה בנפרד — יואב על גוגל, מאיה על מטא, אורי על האתר והקידום האורגני, ליאור על תוכן ומדיה — וכל שיחה נשמרת בחלון משלה.",
        "where_he": "כפתור הצ׳אט הכתום בפינה",
        "access": "self_serve",
        "persona": "general",
        "relevance": {"always": True},
    },
    {
        "key": "human_support",
        "name_he": "תמיכה אנושית בוואטסאפ",
        "what_he": "מעבר לצ׳אט — לחיצה אחת פותחת שיחת וואטסאפ עם בנאדם מהצוות.",
        "where_he": "לשונית ׳תמיכה׳",
        "access": "self_serve",
        "persona": "general",
        "relevance": {"always": True},
    },
    {
        "key": "leads",
        "name_he": "לידים",
        "what_he": "כל מי שהשאיר פרטים אצלכם מגיע לכאן — שם, טלפון, מאיפה הגיע — ואפשר לסמן מה קרה עם כל אחד. אנחנו גם מתקינים טופס באתר שלכם שמזרים לכאן פניות אוטומטית.",
        "where_he": "לשונית ׳לידים׳",
        "access": "automatic",
        "persona": "general",
        "relevance": {"always": True},
    },
    {
        "key": "landing_pages",
        "name_he": "דפי נחיתה",
        "what_he": "דפים קצרים וממוקדים להשארת פרטים, נפרדים מהאתר הראשי. שלושה כלולים בחבילה, ואפשר לחבר אותם לדומיין שלכם.",
        "where_he": "לשונית ׳דפי נחיתה׳",
        "access": "self_serve",
        "persona": "website",
        "relevance": {"always": True},
    },
    {
        "key": "media_hub",
        "name_he": "מרכז המדיה",
        "what_he": "כל התמונות והסרטונים שהופקו עבורכם, במקום אחד, מוכנים להורדה.",
        "where_he": "לשונית ׳מדיה׳",
        "access": "automatic",
        "persona": "media",
        "relevance": {"always": True},
    },
    {
        "key": "personal_area",
        "name_he": "האזור האישי",
        "what_he": "תמונת פרופיל, פרטי החיוב, דוח שקיפות של ההוצאות החיצוניות שלכם (תקציבי פרסום וכלים), וניהול החשבון.",
        "where_he": "לשונית ׳אזור אישי׳",
        "access": "self_serve",
        "persona": "general",
        "relevance": {"always": True},
    },
    {
        "key": "exports",
        "name_he": "ייצוא נתונים",
        "what_he": "כל טבלה בדשבורד — לידים, פעילות, חיובים, מדיה — ניתנת להורדה כ-PDF, אקסל או מסמך גוגל.",
        "where_he": "כפתורי הייצוא מתחת לכל טבלה",
        "access": "self_serve",
        "persona": "general",
        "relevance": {"always": True},
    },
    {
        "key": "journey",
        "name_he": "המסע שלך",
        "what_he": "ציר התקדמות שמראה איפה אתם עומדים — מהרשמה, דרך החיבורים, ועד הליד והמכירה הראשונים.",
        "where_he": "מסך הבית",
        "access": "automatic",
        "persona": "general",
        "relevance": {"always": True},
    },
    {
        "key": "weekly_suggestions",
        "name_he": "הצעות שבועיות ושיעורי בית",
        "what_he": "כל שבוע המערכת מציעה רעיונות שמתאימים לעונה ולתחום שלכם, ולפעמים מבקשת מכם משהו שרק אתם יכולים לתת — תמונה, פרט, אישור.",
        "where_he": "מסך הבית, ׳ממתין לאישור שלך׳",
        "access": "automatic",
        "persona": "general",
        "relevance": {"always": True},
    },
    {
        "key": "content_docs",
        "name_he": "מסמכי תוכן",
        "what_he": "תסריטים מלאים והנחיות ארוכות מגיעים כמסמך גוגל אמיתי בדרייב שלכם, לא כהודעה בצ׳אט.",
        "where_he": "נשלח בצ׳אט עם קישור למסמך",
        "access": "automatic",
        "persona": "media",
        "relevance": {"always": True},
    },
    {
        "key": "external_crm",
        "name_he": "חיבור ל-CRM חיצוני",
        "what_he": "אם כבר יש לכם מערכת CRM (HubSpot, Pipedrive), אפשר לחבר אותה וכל ליד חדש יישלח גם לשם. תוספת, לא החלפה — הלידים ימשיכו להופיע גם אצלנו.",
        "where_he": "לשונית ׳לידים׳, בתחתית",
        "access": "self_serve",
        "persona": "website",
        "relevance": {"always": True},
    },
    {
        "key": "marketing_automation",
        "name_he": "אוטומציה שיווקית (ManyChat / Make)",
        "what_he": "מענה אוטומטי לפניות בוואטסאפ ובאינסטגרם, וחיבור בין המערכות שלכם. זה לא שירות במחיר קבוע ולא מחברים אותו לבד — מדברים איתנו בוואטסאפ, מבינים מה צריך, ורק אז מתמחרים ובונים.",
        "where_he": "מסך הבית, כרטיסי ManyChat ו-Make באזור החיבורים",
        "access": "manual",
        "persona": "general",
        "relevance": {"always": True},
    },

    # ── Package-gated: only for clients who actually bought/connected it ──────
    {
        "key": "google_ads",
        "name_he": "ניהול קמפיינים בגוגל",
        "what_he": "בניית הקמפיינים, אופטימיזציה שוטפת של תקציבים, מילות מפתח ומודעות, ודיווח על מה שעובד.",
        "where_he": "צ׳אט עם יואב",
        "access": "automatic",
        "persona": "google",
        "relevance": {"services": ["google"], "connections": ["google_ads"]},
    },
    {
        "key": "meta_ads",
        "name_he": "ניהול פייסבוק ואינסטגרם",
        "what_he": "קמפיינים ממומנים ותוכן אורגני בפייסבוק ובאינסטגרם, כולל בדיקות קהלים ויצירות.",
        "where_he": "צ׳אט עם מאיה",
        "access": "automatic",
        "persona": "meta",
        "relevance": {"services": ["meta", "facebook", "instagram"],
                      "connections": ["meta_ads", "meta_page", "meta_instagram"]},
    },
    {
        "key": "tiktok",
        "name_he": "טיקטוק",
        "what_he": "הפקת סרטונים קצרים והעלאתם לחשבון שלכם.",
        "where_he": "צ׳אט עם ליאור",
        "access": "automatic",
        "persona": "media",
        "relevance": {"services": ["tiktok"], "connections": ["tiktok"]},
    },
    {
        "key": "youtube",
        "name_he": "ניהול יוטיוב",
        "what_he": "העלאת סרטונים לערוץ שלכם, ניהול הפרסום ומעקב אחרי הצפיות והתגובות.",
        "where_he": "צ׳אט עם ליאור",
        "access": "automatic",
        "persona": "media",
        "relevance": {"services": ["youtube"], "connections": ["youtube"]},
    },
    {
        "key": "organic_seo",
        "name_he": "קידום אורגני",
        "what_he": "מחקר מילות מפתח, כתיבת מאמרים לאתר ושיפורים טכניים — עבודה שמצטברת לאורך חודשים, לא ימים.",
        "where_he": "צ׳אט עם אורי",
        "access": "automatic",
        "persona": "website",
        "relevance": {"services": ["seo", "אורגני", "seoptimer", "semrush", "ahrefs"],
                      "connections": ["seo_tool"]},
    },
    {
        "key": "website",
        "name_he": "האתר שלכם",
        "what_he": "עריכה ופרסום באתר הוורדפרס שלכם, או הקמת אתר חדש אם אין לכם. מאמרים חדשים נכתבים כטיוטות לבדיקה לפני פרסום.",
        "where_he": "מסך הבית, כרטיס ׳האתר שלך׳",
        "access": "self_serve",
        "persona": "website",
        "relevance": {"services": ["website", "אתר"], "connections": ["wordpress"]},
    },
    {
        "key": "media_generation",
        "name_he": "הפקת תמונות וסרטונים",
        "what_he": "יצירת חומרים ויזואליים לעסק שלכם. הכלי עצמו נפתח בחשבון שלכם ומשולם על ידכם ישירות — אנחנו מפעילים אותו בשבילכם.",
        "where_he": "מסך הבית, כרטיס ׳יצירת מדיה׳",
        "access": "self_serve",
        "persona": "media",
        "relevance": {"services": ["media", "content", "video", "תוכן"],
                      "connections": ["higgsfield"]},
    },
    {
        "key": "avatar",
        "name_he": "אווטאר דיגיטלי",
        "what_he": "דמות דיגיטלית שלכם שמגישה סרטונים בלי שתצטרכו לצלם כל פעם מחדש. תוספת בתשלום נפרד, עם אישור מפורש שלכם.",
        "where_he": "מסך הבית, כרטיס ׳אווטאר דיגיטלי׳",
        "access": "self_serve",
        "persona": "media",
        "relevance": {"services": ["avatar", "אווטאר"], "connections": ["heygen"]},
    },
]

_BY_KEY = {f["key"]: f for f in FEATURES}

ACCESS_NOTE_HE = {
    "self_serve": "אפשר להפעיל לבד מהדשבורד",
    "manual": "לא מפעילים לבד — מדברים איתנו קודם",
    "automatic": "רץ אוטומטית, אין מה ללחוץ",
}


def feature(key: str) -> dict:
    return _BY_KEY.get((key or "").strip()) or {}


def feature_keys() -> list:
    return [f["key"] for f in FEATURES]


def _client_signals(client_id: int) -> dict:
    """The two things relevance is judged on: what the client BOUGHT and what
    they actually CONNECTED. Both are reads that already exist elsewhere —
    `required_connections` for the package (it already resolves which package
    was checked out) and `client_accounts` for live connections."""
    services, connections = [], []
    try:
        from core.client_journey import required_connections
        services = required_connections(client_id).get("services") or []
    except Exception as e:
        print(f"[{SERVICE_NAME}] package read failed for client {client_id}: {e}")
    try:
        from agents.client_agent import get_accounts
        connections = [a.get("platform") for a in get_accounts(client_id)
                       if a.get("status") == "active"]
    except Exception as e:
        print(f"[{SERVICE_NAME}] connections read failed for client {client_id}: {e}")
    return {"services": services, "connections": connections}


def _matches(rule: dict, signals: dict) -> bool:
    if rule.get("always"):
        return True
    # Substring match on services, because recommended_services carries free
    # text from the proposal ("קידום אורגני SEOptimer"), not a fixed enum.
    for token in rule.get("services") or []:
        if any(token in service for service in signals["services"]):
            return True
    return any(platform in signals["connections"] for platform in rule.get("connections") or [])


def is_relevant(feature_key: str, client_id: int, signals: dict = None) -> bool:
    """Would this client care about this feature? Package first, then live
    connections — so a client who connected YouTube without it being in their
    package still counts as relevant, and a client with neither does not.

    Unknown key returns False: an announcement pointing at a feature that no
    longer exists must reach nobody, not everybody."""
    entry = feature(feature_key)
    if not entry:
        return False
    return _matches(entry["relevance"], signals if signals is not None else _client_signals(client_id))


def relevant_features(client_id: int) -> list:
    signals = _client_signals(client_id)
    return [f for f in FEATURES if _matches(f["relevance"], signals)]


def catalog_for_prompt(client_id: int = None) -> str:
    """The catalogue as prompt text. With a client_id, only what applies to
    THEM — the interview must not describe a YouTube add-on to someone who
    doesn't have one. Without one, everything (used where no client context
    exists yet).

    Deliberately compact: this goes into a system prompt where every line is
    latency, and the `access` note is the part that matters most — describing a
    manual service as if it were a button is the specific error this prevents.
    """
    entries = relevant_features(client_id) if client_id else FEATURES
    lines = []
    for f in entries:
        where = f" [{f['where_he']}]" if f["where_he"] else ""
        lines.append(f"- {f['name_he']}{where}: {f['what_he']} "
                     f"({ACCESS_NOTE_HE.get(f['access'], '')})")
    if client_id:
        log_step(SERVICE_NAME, "catalog_for_prompt",
                 f"client {client_id}: {len(entries)}/{len(FEATURES)} features relevant")
    return "\n".join(lines)
