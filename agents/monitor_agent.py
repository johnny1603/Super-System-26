import json
import os
from datetime import datetime

from anthropic import Anthropic
from google.cloud import pubsub_v1
from supabase import create_client

from config.settings import PROJECT_ID

client = Anthropic()
db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALERT_HISTORY_PATH = os.path.join(BASE_DIR, "data", "alert_history.json")
MONITOR_MEMORY_PATH = os.path.join(BASE_DIR, "data", "monitor_memory.json")
SUBSCRIPTION_ID = "alerts-sub"

CRITICAL_KEYWORDS = ["crash", "exception", "500", "failed", "error", "approved=false", "api error"]

DEEP_SCAN_SYSTEM = """You are a business intelligence monitor for uallak, an Israeli marketing agency.
You receive a structured snapshot of the onboarding system's performance: lead counts, rejection rates,
budget patterns, business types, and recent alert history.

Your tasks:
1. Identify meaningful patterns — not just raw numbers, but what they imply about the business or the system
2. For each finding, suggest a concrete explanation or recommended action
3. Classify each finding:
   - "urgent": needs immediate human attention (e.g. >50% rejection rate, repeated API failures, same business type always rejected, math errors in proposals)
   - "insight": useful trend for a weekly digest (e.g. most rejections cluster around a budget range, a service is never recommended)
4. Skip any finding whose issue_id appears in previously_reported_issue_ids — do not repeat known issues
5. Assign each finding a short stable snake_case issue_id (e.g. "high_rejection_low_budget", "google_ads_underutilized")

Return JSON only:
{
  "urgent": [
    {"issue_id": "string", "finding": "string", "suggestion": "string"}
  ],
  "insights": [
    {"issue_id": "string", "finding": "string", "suggestion": "string"}
  ]
}

Return empty arrays if there is nothing meaningful to report. Do not invent issues."""


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _append_alert_history(entry: dict):
    history = _load_json(ALERT_HISTORY_PATH, [])
    history.append(entry)
    _save_json(ALERT_HISTORY_PATH, history[-500:])


# ─── PART 1: Passive Pub/Sub listener ────────────────────────────────────────

def listen_for_critical_alerts():
    """Subscribe to the alerts-sub Pub/Sub subscription.

    Returns the StreamingPullFuture. Call .result() on it to block,
    or store it and call .cancel() to stop listening.
    """
    subscriber = pubsub_v1.SubscriberClient()
    subscription_path = subscriber.subscription_path(PROJECT_ID, SUBSCRIPTION_ID)

    def _callback(message):
        ts = datetime.now().isoformat()
        try:
            text = message.data.decode("utf-8")
        except Exception:
            text = str(message.data)

        is_critical = any(kw in text.lower() for kw in CRITICAL_KEYWORDS)
        tag = "CRITICAL" if is_critical else "INFO"
        print(f"[{ts}] MONITOR [{tag}] {text}")

        if is_critical:
            _append_alert_history({"ts": ts, "source": "pubsub", "message": text})

        message.ack()

    future = subscriber.subscribe(subscription_path, callback=_callback)
    print(f"[MONITOR] Listening on {subscription_path} ...")
    return future


# ─── PART 2: Scheduled deep scan ─────────────────────────────────────────────

def _read_leads() -> list[dict]:
    result = db.table("leads").select("*").order("created_at", desc=True).execute()
    return result.data or []


def run_deep_scan() -> dict:
    leads = _read_leads()
    alert_history = _load_json(ALERT_HISTORY_PATH, [])
    memory = _load_json(MONITOR_MEMORY_PATH, {"reported_issues": [], "last_scan": None})

    total = len(leads)
    if total == 0:
        return {
            "urgent": [],
            "insights": [{"issue_id": "no_leads_yet", "finding": "No leads in the database yet.",
                          "suggestion": "System is running but no onboarding completions have been recorded."}]
        }

    approved_count = sum(1 for l in leads if l.get("approved"))
    rejected_count = total - approved_count
    rejection_rate = round(rejected_count / total * 100, 1)

    # Summarise leads for Claude — extract business type and budget without leaking PII
    business_types, budget_ranges, rejected_budgets, rejected_business_types = [], [], [], []
    repeated_review_issues = {}

    for lead in leads:
        try:
            answers = lead.get("answers") or {}
            btype = (answers.get("business_type") or answers.get("intro", ""))[:80]
            budget = answers.get("marketing_budget") or answers.get("budget", "unknown")
            business_types.append(btype)
            budget_ranges.append(budget)
            if not lead.get("approved"):
                rejected_budgets.append(budget)
                rejected_business_types.append(btype)
        except Exception:
            pass

    # Count repeated issues from reviewer alerts
    for entry in alert_history:
        if entry.get("source") == "review":
            for issue in entry.get("issues", []):
                repeated_review_issues[issue] = repeated_review_issues.get(issue, 0) + 1

    # Identify proposal fields that commonly appear inconsistent across rejected leads
    fee_mismatches = 0
    for lead in leads:
        try:
            proposal = lead.get("proposal") or {}
            monthly = int(proposal.get("monthly_management_total") or 0)
            benefit = int(proposal.get("benefit_value") or 0)
            if monthly > 0 and benefit != monthly * 2:
                fee_mismatches += 1
        except Exception:
            pass

    data_summary = {
        "total_leads": total,
        "approved": approved_count,
        "rejected": rejected_count,
        "rejection_rate_pct": rejection_rate,
        "business_types_sample": business_types[:30],
        "budget_ranges_all": budget_ranges[:30],
        "rejected_budgets": rejected_budgets[:20],
        "rejected_business_types": rejected_business_types[:20],
        "repeated_reviewer_issues": repeated_review_issues,
        "proposals_with_benefit_mismatch": fee_mismatches,
        "recent_pubsub_alerts": [e for e in alert_history[-30:] if e.get("source") == "pubsub"],
        "previously_reported_issue_ids": memory.get("reported_issues", []),
    }

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=DEEP_SCAN_SYSTEM,
        messages=[{"role": "user", "content": f"System snapshot:\n{json.dumps(data_summary, ensure_ascii=False, indent=2)}"}]
    )
    raw = response.content[0].text.replace("```json", "").replace("```", "").strip()
    report = json.loads(raw)

    # Persist newly reported issue_ids so they're not repeated next scan
    new_ids = [f["issue_id"] for f in report.get("urgent", []) + report.get("insights", [])]
    memory["reported_issues"] = list(set(memory.get("reported_issues", []) + new_ids))
    memory["last_scan"] = datetime.now().isoformat()
    _save_json(MONITOR_MEMORY_PATH, memory)

    urgent_count = len(report.get("urgent", []))
    insight_count = len(report.get("insights", []))
    print(f"[{datetime.now().isoformat()}] DEEP SCAN complete — urgent={urgent_count} insights={insight_count}")

    return report
