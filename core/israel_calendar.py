"""Israeli marketing calendar — the data source for proactive holiday/season
engagement (agents/engagement_agent.py).

DELIBERATELY a static, human-verified table (dates cross-checked against
hebcal.com, 2026-07) rather than a Hebrew-calendar computation library:
nothing runs locally on the dev machine, so a subtly wrong date arithmetic
bug would ship straight to clients — a table we can read is safer than code
we can't test. The cost: it runs dry. horizon_warning() exists so the weekly
engagement run alerts the team ~90 days before the last known date, and
extending it is a 5-minute edit against hebcal.

Generalized from the per-client Israeli seasonal campaign calendar first
built for the Shin Sekai Instagram work — same concept, system-wide.

Event kinds:
- holiday   — promotional opportunity anchored to a chag
- shopping  — commercial season (Black Friday, back-to-school...)
- seasonal  — long windows (summer, school breaks)
- sensitive — days to TONE DOWN: pause upbeat promos, no sales pushes

industries: [] = relevant to (almost) everyone; otherwise loose tags the
LLM matches against the client's business ("food", "retail", "gifts",
"tourism", "kids", "b2b", "beauty", "events", "home"). These are hints for
the prompt, not a hard filter — the engagement agent passes the event + the
client's business description and lets the model judge relevance.
"""
from datetime import date, datetime, timedelta

# lead_days = how far ahead preparation should start (suggestion appears in
# the client's queue once today >= event_date - lead_days). Big shopping
# moments get 21 days (creative + campaign lead time), regular chagim 14.
EVENTS = [
    # ── Chagim (start dates = erev chag, verified against hebcal) ───────────
    {"slug": "rosh_hashana", "kind": "holiday", "name_he": "ראש השנה",
     "dates": ["2026-09-11", "2027-10-01", "2028-09-20"], "lead_days": 21,
     "industries": ["food", "gifts", "retail", "events", "home"],
     "angle_he": "מארזי שי ומבצעי חג, ברכות ללקוחות, שיא קניות מתנות ואירוח לקראת החג"},
    {"slug": "yom_kippur", "kind": "sensitive", "name_he": "יום כיפור",
     "dates": ["2026-09-20", "2027-10-10", "2028-09-29"], "lead_days": 7,
     "industries": [],
     "angle_he": "להשהות קמפיינים ופרסום קליל ביום עצמו; תוכן מכבד בלבד, בלי מבצעים"},
    {"slug": "sukkot", "kind": "holiday", "name_he": "סוכות",
     "dates": ["2026-09-25", "2027-10-15", "2028-10-04"], "lead_days": 14,
     "industries": ["tourism", "events", "kids", "food", "home"],
     "angle_he": "חול המועד = משפחות מטיילות ומבלות; אטרקציות, אירוח, פעילויות ילדים"},
    {"slug": "hanukkah", "kind": "holiday", "name_he": "חנוכה",
     "dates": ["2026-12-04", "2027-12-24", "2028-12-12"], "lead_days": 14,
     "industries": ["kids", "food", "retail", "gifts", "events"],
     "angle_he": "שבוע חופש מהלימודים — פעילויות ילדים, סופגניות ומתוקים, מתנות קטנות"},
    {"slug": "tu_bishvat", "kind": "holiday", "name_he": 'ט"ו בשבט',
     "dates": ["2027-01-22", "2028-02-11"], "lead_days": 10,
     "industries": ["food", "home", "gifts"],
     "angle_he": "טבע וקיימות, פירות יבשים ומארזים ירוקים; זווית סביבתית למותג"},
    {"slug": "purim", "kind": "holiday", "name_he": "פורים",
     "dates": ["2027-03-22", "2028-03-11"], "lead_days": 21,
     "industries": ["kids", "retail", "events", "food", "beauty"],
     "angle_he": "תחפושות, משלוחי מנות, מסיבות — קניות מתחילות שבועות מראש"},
    {"slug": "pesach", "kind": "holiday", "name_he": "פסח",
     "dates": ["2027-04-21", "2028-04-10"], "lead_days": 21,
     "industries": ["food", "home", "tourism", "retail", "events"],
     "angle_he": "ניקיון ושדרוג הבית, אירוח סדר, חופשת חול המועד — שיא הוצאה משפחתית"},
    {"slug": "yom_hazikaron", "kind": "sensitive", "name_he": "יום הזיכרון",
     "dates": ["2027-05-10", "2028-04-30"], "lead_days": 7,
     "industries": [],
     "angle_he": "להשהות מבצעים ותוכן שיווקי קליל; פרסום מאופק בלבד עד צאת היום"},
    {"slug": "yom_haatzmaut", "kind": "holiday", "name_he": "יום העצמאות",
     "dates": ["2027-05-11", "2028-05-01"], "lead_days": 14,
     "industries": ["food", "events", "retail", "tourism"],
     "angle_he": "על האש, מסיבות ומבצעי עצמאות — מעבר חד מהיום שלפני; לתזמן בזהירות"},
    {"slug": "lag_baomer", "kind": "holiday", "name_he": 'ל"ג בעומר',
     "dates": ["2027-05-24", "2028-05-13"], "lead_days": 10,
     "industries": ["kids", "food", "events"],
     "angle_he": "מדורות ופעילות משפחתית בערב; ציוד, אוכל מוכן, אירועים"},
    {"slug": "shavuot", "kind": "holiday", "name_he": "שבועות",
     "dates": ["2027-06-10", "2028-05-30"], "lead_days": 14,
     "industries": ["food", "events", "home"],
     "angle_he": "חלבי ולבן — מתכונים, מארזי גבינות, אירוח; אירועי קהילה"},
    {"slug": "tu_beav", "kind": "holiday", "name_he": 'ט"ו באב',
     "dates": ["2027-08-17", "2028-08-06"], "lead_days": 10,
     "industries": ["gifts", "beauty", "food", "events"],
     "angle_he": "חג האהבה הישראלי — מתנות, ערבים זוגיים, מבצעי זוגות"},

    # ── Commercial / civil ───────────────────────────────────────────────────
    {"slug": "back_to_school", "kind": "shopping", "name_he": "חזרה ללימודים",
     "dates": ["2026-09-01", "2027-09-01", "2028-09-01"], "lead_days": 21,
     "industries": ["kids", "retail"],
     "angle_he": "ציוד, ביגוד וחוגים — ההוצאה המשפחתית הגדולה של סוף הקיץ"},
    {"slug": "november_sales", "kind": "shopping", "name_he": "מבצעי נובמבר (11.11 עד בלאק פריידיי)",
     "dates": ["2026-11-11", "2027-11-11", "2028-11-11"], "lead_days": 21,
     "industries": ["retail", "beauty", "home"],
     "angle_he": "חודש הקניות של השנה: 11.11, שופינג IL ובלאק פריידיי בסופו — מי שלא נערך מראש נעלם ברעש"},
    {"slug": "black_friday", "kind": "shopping", "name_he": "בלאק פריידיי",
     "dates": ["2026-11-27", "2027-11-26", "2028-11-24"], "lead_days": 14,
     "industries": ["retail", "beauty", "home"],
     "angle_he": "שיא המבצעים — דיל ברור אחד חזק עדיף על עשרה קטנים"},
    {"slug": "valentines", "kind": "shopping", "name_he": "ולנטיין",
     "dates": ["2027-02-14", "2028-02-14"], "lead_days": 10,
     "industries": ["gifts", "beauty", "food", "events"],
     "angle_he": "ערבים זוגיים ומתנות — רלוונטי בעיקר לקהל צעיר ועירוני"},
    {"slug": "summer_season", "kind": "seasonal", "name_he": "החופש הגדול",
     "dates": ["2027-07-01", "2028-07-01"], "lead_days": 21,
     "industries": ["kids", "tourism", "events", "food"],
     "angle_he": "חודשיים של ילדים בבית — קייטנות, אטרקציות, פעילויות; ותנועה חלשה ב-B2B"},
    {"slug": "year_end_b2b", "kind": "seasonal", "name_he": "סוף שנת המס",
     "dates": ["2026-12-15", "2027-12-15", "2028-12-15"], "lead_days": 14,
     "industries": ["b2b"],
     "angle_he": "עסקים סוגרים תקציבים והוצאות מוכרות לפני 31.12 — חלון טוב להצעות B2B"},
]

# Alert this many days before the table's last known date — enough time to
# extend it calmly (see module docstring for why it's static).
HORIZON_WARNING_DAYS = 90


def _parse(d: str) -> date:
    return datetime.strptime(d, "%Y-%m-%d").date()


def upcoming_events(today: date = None) -> list:
    """Events inside their preparation window right now: for each occurrence,
    included when event_date - lead_days <= today <= event_date. Returns
    dicts with days_until + the event's marketing fields, soonest first."""
    today = today or date.today()
    window = []
    for event in EVENTS:
        for occurrence in event["dates"]:
            event_date = _parse(occurrence)
            days_until = (event_date - today).days
            if 0 <= days_until <= event["lead_days"]:
                window.append({
                    "slug": event["slug"], "kind": event["kind"],
                    "name_he": event["name_he"], "date": occurrence,
                    "days_until": days_until, "industries": event["industries"],
                    "angle_he": event["angle_he"],
                })
    window.sort(key=lambda e: e["days_until"])
    return window


def horizon_warning(today: date = None) -> str:
    """Non-empty warning string when the static table is close to running dry
    — the weekly engagement run surfaces it as an alert."""
    today = today or date.today()
    last = max(_parse(d) for event in EVENTS for d in event["dates"])
    if last - today <= timedelta(days=HORIZON_WARNING_DAYS):
        return (f"israel_calendar table ends {last.isoformat()} — extend it "
                "(verify new dates against hebcal.com) before it runs dry")
    return ""
