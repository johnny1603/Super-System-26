"""uallak's OWN operating costs — "what am I actually spending", company-wide.

## What this is NOT (three neighbours it is easy to confuse)

- **`core/cost_tracker.py` / `client_costs`** — per-CLIENT AI usage cost. This
  module READS that (aggregated) as one line item; it never records a cost.
- **`agents/budget_agent.py`** — ONE client's financial picture (their ad spend,
  their tools, their margin). Per-client. This module is per-COMPANY.
- **`core/admin_service.get_overview()`** — revenue/MRR/margin, which already
  includes the `client_costs` total. That view answers "is the business
  profitable"; this one answers "where does my money go".

## The honesty rule this module exists to enforce

The brief asked for "real numbers pulled from each provider". After checking
every service we have credentials for, that is **not achievable for almost any
of them** — SaaS billing APIs are rare, and the ones that exist need separate
admin/billing credentials we do not hold. Pretending otherwise would be the
worst possible outcome for a page whose entire job is telling Johnny the truth
about his spend.

So every line carries a `tracking` label, and the UI shows it:

- **`measured`** — computed from usage WE actually recorded. Today that is only
  Claude: real token counts from every LLM call, priced at list rates.
  Honest caveat, stated in the row itself: this is *our arithmetic on real
  usage*, NOT a figure pulled from Anthropic's billing. It ignores discounts,
  prompt-caching credits and the real USD→ILS rate on the day.
- **`derived`** — a real live COUNT multiplied by a known rate. Today: InstaWP
  sites (count of provisioned sites is real; $/site is a reference price).
- **`manual`** — a number a human typed, with the date they last confirmed it.
  Ages visibly so a stale figure looks stale.
- **`none`** — genuinely free at our scale (quota-limited APIs). Listed on
  purpose: "why isn't Meta here?" is a question worth answering in the UI
  rather than in someone's head.

**No line in this module is a live billing pull, and none claims to be.** If a
provider ever exposes one, it upgrades that row to a new `live` label — do not
quietly relabel `manual` as live.

## Monthly total: two halves, deliberately not blended into one number

- **Fixed** — recurring subscriptions (monthly, or yearly ÷ 12).
- **Variable** — this calendar month's usage so far, which is incomplete by
  definition until the month ends.

They are reported separately AND summed, with the sum labelled as
"month-to-date", because adding a full month's rent to 3 days of API usage and
calling it "monthly cost" is exactly the kind of confident-wrong number this
codebase keeps banning elsewhere.
"""
import os
from datetime import datetime, timezone

from core.agent_base import log_step

SERVICE_NAME = "operating_costs"

# Kept in sync with agents/keys_agent.py KEYS by hand — every credential there
# that implies a paid (or notably free) service should appear here, so the page
# answers "is that all of it?" honestly. A service with no key is still listed
# when we pay for it (Cloud Run, domains) — credentials are not the same thing
# as costs.
CATEGORIES = {
    "ai": "AI ומודלים",
    "infra": "תשתית ואחסון",
    "hosting": "אחסון אתרי לקוחות",
    "comms": "תקשורת והתראות",
    "platform_apis": "ממשקי פלטפורמות",
    "payments": "תשלומים",
}

SERVICES = [
    {
        "key": "anthropic_claude", "label": "Claude API (Anthropic)", "category": "ai",
        "tracking": "measured", "billable": True,
        "note": ("מחושב מספירת הטוקנים האמיתית של כל קריאה, לפי מחירון רשמי ושער דולר "
                 "קבוע — לא נשלף מהחיוב של Anthropic."),
        "upgrade": ("Anthropic's Admin API exposes a real cost report, but it needs a separate "
                    "admin key (sk-ant-admin...) that is not configured. Wiring it would turn "
                    "this row from `measured` into a genuine billing pull."),
    },
    {
        "key": "instawp", "label": "InstaWP — אחסון אתרים", "category": "hosting",
        "tracking": "derived", "billable": True,
        "note": "מספר האתרים אמיתי; המחיר לאתר הוא מחירון ידוע, לא חשבונית.",
        "upgrade": ("InstaWP's v2 API covers sites/templates/teams only — no billing or "
                    "subscription endpoint. Nothing to wire; the site count is the honest part."),
    },
    {
        "key": "supabase", "label": "Supabase (בסיס נתונים)", "category": "infra",
        "tracking": "manual", "billable": True,
        "note": "עלות התוכנית. השימוש בפועל נמדד בנפרד ומוצג לצד המספר.",
        "upgrade": ("Supabase's Management API can read the org subscription, but it needs a "
                    "personal access token — the service key we hold cannot. A real upgrade path "
                    "if the plan ever stops being free."),
    },
    {
        "key": "google_cloud_run", "label": "Google Cloud Run + Artifact Registry", "category": "infra",
        "tracking": "manual", "billable": True,
        "note": "חיוב לפי שימוש. נמוך בקנה המידה הנוכחי, אך לא אפס.",
        "upgrade": ("The Cloud Billing API is real, but reading it needs a service account with "
                    "billing-account IAM. Ours (GOOGLE_SERVICE_ACCOUNT_JSON) is Drive-scoped. "
                    "The single most worthwhile live integration to add here."),
    },
    {
        "key": "cloudflare", "label": "Cloudflare", "category": "infra",
        "tracking": "manual", "billable": True,
        "note": ("חינם בקנה המידה הנוכחי. Cloudflare for SaaS כולל 100 דומיינים מותאמים "
                 "בחינם — מעבר לזה יש עלות לכל דומיין."),
        "upgrade": ("Cloudflare's billing endpoints are account-level and largely deprecated; "
                    "the practical watch item is the custom-hostname count in core/landing_domains.py, "
                    "not a billing call."),
    },
    {
        "key": "domains", "label": "דומיינים (uallak.com ואחרים)", "category": "infra",
        "tracking": "manual", "billable": True,
        "note": "חידוש שנתי — הזן כעלות שנתית והמערכת תחלק ל-12.",
        "upgrade": "Registrar APIs vary and none is integrated. Manual by nature.",
    },
    {
        "key": "green_api_whatsapp", "label": "Green API (וואטסאפ)", "category": "comms",
        "tracking": "manual", "billable": True,
        "note": "ערוץ ה-SOS. תלוי תוכנית — יש שכבת חינם מוגבלת.",
        "upgrade": ("Green API exposes instance state, not billing. Manual by nature."),
    },
    {
        "key": "google_workspace", "label": "Gmail / Google Workspace", "category": "comms",
        "tracking": "manual", "billable": True,
        "note": "אם המערכת שולחת מחשבון Workspace בתשלום ולא מ-Gmail אישי.",
        "upgrade": "Workspace billing has no practical API for a single-seat account. Manual.",
    },
    {
        "key": "paypal_fees", "label": "עמלות PayPal", "category": "payments",
        "tracking": "manual", "billable": True,
        "note": ("עמלה לכל עסקה. כרגע Sandbox בלבד — העלות האמיתית היא 0 עד המעבר ל-Live "
                 "(core/paypal_service.py מקובע ל-Sandbox)."),
        "upgrade": ("PayPal's Transaction Search API does report real fees per transaction — "
                    "genuinely wireable, but pointless until the account goes Live."),
    },
    {
        "key": "platform_apis", "label": "Google Ads / Meta / TikTok / YouTube APIs", "category": "platform_apis",
        "tracking": "none", "billable": False,
        "note": ("ללא עלות כספית — מוגבלות במכסות בלבד. תקציבי הפרסום עצמם משולמים "
                 "ישירות על ידי הלקוחות ואינם עלות שלנו."),
        "upgrade": "",
    },
    {
        "key": "client_paid_tools", "label": "Higgsfield / HeyGen / ElevenLabs / כלי SEO", "category": "platform_apis",
        "tracking": "none", "billable": False,
        "note": ("משולמים ישירות על ידי הלקוח בכרטיס שלו — לעולם לא עלות שלנו "
                 "(ראה budget_agent לתמונה הלקוחית)."),
        "upgrade": "",
    },
]

_MONTHLY_DIVISOR = {"monthly": 1.0, "yearly": 12.0}

_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        from supabase import create_client
        _db_instance = create_client(os.environ["SUPABASE_URL"],
                                     os.environ["SUPABASE_SERVICE_KEY"])
    return _db_instance


def _month_start() -> str:
    return (datetime.now(timezone.utc)
            .replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat())


# ─── The three ways a number gets here ────────────────────────────────────────

def _measured_claude_cost() -> dict:
    """This month's real Claude spend, from the same `client_costs` rows the
    admin overview already totals. Read, never re-recorded — cost_tracker stays
    the only writer."""
    try:
        rows = (_db().table("client_costs").select("category, amount")
                .gte("created_at", _month_start()).limit(5000).execute().data or [])
    except Exception as e:
        print(f"[{SERVICE_NAME}] client_costs read failed: {e}")
        return {"amount_ils": None, "available": False, "breakdown": {}}
    breakdown = {}
    for row in rows:
        category = row.get("category") or "other"
        breakdown[category] = round(breakdown.get(category, 0) + (row.get("amount") or 0), 2)
    return {
        "amount_ils": round(sum(breakdown.values()), 2),
        "available": True,
        "breakdown": breakdown,
        "rows": len(rows),
    }


def _provisioned_site_count() -> int:
    """Distinct clients with a site we provisioned on InstaWP. Real count, with
    one honest caveat carried into the UI: nothing here notices a site deleted
    by hand in InstaWP's console, because no deprovision flow exists."""
    try:
        rows = (_db().table("client_activity").select("client_id")
                .eq("action_type", "website_provisioned").limit(1000).execute().data or [])
    except Exception as e:
        print(f"[{SERVICE_NAME}] provisioned-site count failed: {e}")
        return 0
    return len({row.get("client_id") for row in rows if row.get("client_id") is not None})


def _derived_instawp_cost() -> dict:
    """Sites × our known per-site cost (PRICING — the single source of truth for
    that number, not a copy)."""
    from agents.onboarding_agent import PRICING
    hosting = (PRICING.get("website") or {}).get("new_site_hosting") or {}
    per_site = hosting.get("cost_monthly_ils") or 0
    sites = _provisioned_site_count()
    return {
        "amount_ils": round(sites * per_site, 2),
        "sites": sites,
        "per_site_ils": per_site,
        "detail": f"{sites} אתרים × ₪{per_site}",
    }


def _manual_entries() -> tuple:
    """(rows_by_key, table_exists). A missing table is a SUPPORTED state — the
    page still renders every measured/derived number and says which migration
    to run, rather than 500ing or showing zeros that read as 'free'."""
    try:
        rows = _db().table("operating_costs").select("*").execute().data or []
    except Exception as e:
        print(f"[{SERVICE_NAME}] operating_costs unavailable (migration not run?): {e}")
        return {}, False
    return {row["service_key"]: row for row in rows}, True


def _monthly_from_entry(entry: dict) -> float:
    amount = float(entry.get("amount_ils") or 0)
    return round(amount / _MONTHLY_DIVISOR.get(entry.get("cadence") or "monthly", 1.0), 2)


# ─── The one entry point ──────────────────────────────────────────────────────

def get_operating_costs() -> dict:
    """Every operating cost uallak carries, one row per service, each labelled
    with HOW its number was obtained. Never raises — a broken source degrades to
    that row reporting itself unavailable."""
    log_step(SERVICE_NAME, "get_operating_costs", "building company-wide cost view")
    manual_rows, table_exists = _manual_entries()
    measured = _measured_claude_cost()
    instawp = _derived_instawp_cost()

    lines, unset = [], []
    fixed_total = variable_total = 0.0

    for service in SERVICES:
        line = {
            "key": service["key"], "label": service["label"],
            "category": service["category"],
            "category_label": CATEGORIES.get(service["category"], service["category"]),
            "tracking": service["tracking"], "billable": service["billable"],
            "note": service["note"], "upgrade_path": service.get("upgrade", ""),
            "amount_ils": None, "is_set": False, "detail": "", "kind": "fixed",
        }

        if service["tracking"] == "measured":
            # Variable: this is month-to-date usage, not a subscription
            line["kind"] = "variable"
            line["amount_ils"] = measured["amount_ils"]
            line["is_set"] = measured["available"]
            line["detail"] = (f"{measured.get('rows', 0)} קריאות מתועדות החודש"
                              if measured["available"] else "נתוני שימוש לא זמינים")
            line["breakdown"] = measured.get("breakdown", {})
            if measured["available"]:
                variable_total += measured["amount_ils"] or 0

        elif service["tracking"] == "derived":
            line["amount_ils"] = instawp["amount_ils"]
            line["is_set"] = True
            line["detail"] = instawp["detail"]
            line["caveat"] = ("אתר שנמחק ידנית ב-InstaWP לא ייספר כאן — "
                              "אין תהליך ביטול אוטומטי במערכת.")
            fixed_total += instawp["amount_ils"]

        elif service["tracking"] == "manual":
            entry = manual_rows.get(service["key"])
            if entry:
                monthly = _monthly_from_entry(entry)
                line["amount_ils"] = monthly
                line["is_set"] = True
                line["cadence"] = entry.get("cadence", "monthly")
                line["raw_amount_ils"] = float(entry.get("amount_ils") or 0)
                line["confirmed_at"] = entry.get("confirmed_at", "")
                line["admin_note"] = entry.get("note", "")
                line["detail"] = ("חיוב שנתי — מחולק ל-12"
                                  if entry.get("cadence") == "yearly" else "חיוב חודשי")
                fixed_total += monthly
            else:
                # Deliberately NOT 0: "nobody has entered this" and "this is free"
                # are different facts, and only one of them is good news.
                unset.append(service["label"])
                line["detail"] = "לא הוגדר עדיין"

        else:  # tracking == "none" — genuinely free, listed to answer the question
            line["amount_ils"] = 0
            line["is_set"] = True
            line["detail"] = "ללא עלות"

        lines.append(line)

    return {
        "lines": lines,
        "categories": CATEGORIES,
        "totals": {
            "fixed_monthly_ils": round(fixed_total, 2),
            "variable_month_to_date_ils": round(variable_total, 2),
            "month_to_date_ils": round(fixed_total + variable_total, 2),
        },
        "by_category": _totals_by_category(lines),
        "unset_services": unset,
        "manual_table_ready": table_exists,
        "migration": ("migrations/2026-08-03-operating-costs.sql" if not table_exists else ""),
        "month": datetime.now(timezone.utc).strftime("%Y-%m"),
        # Said once, here, so no consumer has to infer it
        "disclaimer": ("אף מספר בעמוד הזה אינו נשלף ממערכת החיוב של הספק. "
                       "מדוד = חישוב שלנו על שימוש אמיתי; נגזר = ספירה אמיתית × מחירון; "
                       "ידני = מספר שהוזן והתאריך שבו אושר לאחרונה."),
    }


def _totals_by_category(lines: list) -> list:
    totals = {}
    for line in lines:
        if line["amount_ils"] is None:
            continue
        key = line["category"]
        entry = totals.setdefault(key, {"category": key,
                                        "label": CATEGORIES.get(key, key),
                                        "amount_ils": 0, "services": 0})
        entry["amount_ils"] = round(entry["amount_ils"] + line["amount_ils"], 2)
        entry["services"] += 1
    return sorted(totals.values(), key=lambda entry: entry["amount_ils"], reverse=True)


def set_manual_cost(service_key: str, amount_ils: float, cadence: str = "monthly",
                    note: str = "") -> dict:
    """Set/confirm one manually maintained cost. Re-saving an unchanged number is
    a legitimate action — it refreshes `confirmed_at`, which is how a figure
    stops looking stale."""
    known = {s["key"] for s in SERVICES if s["tracking"] == "manual"}
    if service_key not in known:
        return {"success": False, "error": f"unknown or non-manual service: {service_key}"}
    if cadence not in _MONTHLY_DIVISOR:
        return {"success": False, "error": f"cadence must be one of {list(_MONTHLY_DIVISOR)}"}
    try:
        amount = round(float(amount_ils), 2)
    except (TypeError, ValueError):
        return {"success": False, "error": "amount_ils must be a number"}
    if amount < 0:
        return {"success": False, "error": "amount_ils cannot be negative"}

    now = datetime.now(timezone.utc).isoformat()
    try:
        _db().table("operating_costs").upsert({
            "service_key": service_key, "amount_ils": amount,
            "cadence": cadence, "note": (note or "")[:500],
            "confirmed_at": now, "updated_at": now,
        }, on_conflict="service_key").execute()
    except Exception as e:
        print(f"[{SERVICE_NAME}] could not save {service_key}: {e}")
        return {"success": False, "error": str(e),
                "hint": "run migrations/2026-08-03-operating-costs.sql"}
    log_step(SERVICE_NAME, "set_manual_cost", f"{service_key} = {amount} ({cadence})")
    return {"success": True, "service_key": service_key, "amount_ils": amount,
            "cadence": cadence, "confirmed_at": now}
