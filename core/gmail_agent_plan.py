"""The Gmail agent package — who may use it, what it may cost, and the brakes.
(Design: docs/PROSPECTING.md. Price and margin: PRICING["gmail_agent"].)

ENTITLEMENT is decided SERVER-SIDE here, never in a UI, the same way the
organic and hosting entitlements are: client_journey._chosen_package is the
one answer to "what did this client buy" (a checkout package or an
admin-created account's grant), and it FAILS CLOSED — an unreadable package
is a "no". Internal accounts (billing_exempt — uallak's own client
acquisition) may also be switched on by the admin (client_settings
gmail_agent_internal_access).

THE MARGIN IS ENFORCED BY COST, NOT BY COUNTS. Every paid call this package
makes runs inside `cost_action(FEATURE)`, so its client_costs row — Places
SKU requests (places_service), LLM tokens (claude_json), web-search fees —
carries details.action = FEATURE under that client. `require_budget` sums
this calendar month's rows (UTC) and refuses a paid call that would pass the
monthly budget: PRICING's GMAIL_AGENT_MONTHLY_COST_BUDGET_NIS for a paying
client, client_settings gmail_agent_internal_budget_nis for an internal one.
The admin's own list views are tagged ADMIN_FEATURE: they are our cost, not
the client's allowance.

SECONDARY BRAKES (counts, api_call_counters, per UTC day): Places requests
per client and overall, approved messages per client and overall.
"""
from datetime import datetime, timezone

FEATURE = "gmail_agent"
ADMIN_FEATURE = "gmail_agent_admin"

PLACES_PER_CLIENT_PER_DAY = 60
PLACES_GLOBAL_PER_DAY = 600
MESSAGES_PER_CLIENT_PER_DAY = 10   # Gmail send hard cap (Google round 2 brief: ~10/day per client)
MESSAGES_GLOBAL_PER_DAY = 20


class BudgetExhausted(Exception):
    def __init__(self, spent: float, budget: float):
        super().__init__(f"monthly budget used: {spent:.2f}/{budget:.2f} NIS")
        self.spent, self.budget = spent, budget


class CapReached(Exception):
    def __init__(self, which: str):
        super().__init__(f"daily cap reached: {which}")
        self.which = which


# ─── Seams (patched by tests) ────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _client(client_id: int) -> dict:
    from agents.client_agent import get_client
    return get_client(client_id) or {}


def _package(client_id: int) -> dict:
    from core.client_journey import _chosen_package
    return _chosen_package(client_id) or {}


def _settings(client_id: int) -> dict:
    from core import client_settings
    return client_settings.get(client_id)


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _count(counter: str) -> int:
    """Increment today's counter and return today's total (fails open: 0)."""
    from core.api_call_counters import increment_call_counter
    return increment_call_counter(counter, window_days=1)


def _pricing() -> dict:
    from agents.onboarding_agent import PRICING
    return PRICING["gmail_agent"]


# ─── Entitlement ─────────────────────────────────────────────────────────────

def entitled(client_id: int) -> bool:
    """Server-side, fail closed. Never trust a UI for this."""
    try:
        client = _client(client_id)
        if not client:
            return False
        spec = _pricing()
        package = _package(client_id)
        services = [str(s).lower() for s in package.get("recommended_services") or []]
        if any(spec["service_token"] in s for s in services):
            return True
        if spec["label_he"] in " ".join(str(k) for k in (package.get("monthly_breakdown") or {})):
            return True
        return client.get("billing_exempt") is True and bool(_settings(client_id).get("gmail_agent_internal_access"))
    except Exception as e:
        print(f"[gmail_agent_plan] entitlement check failed for client {client_id} — refusing: {type(e).__name__}")
        return False


# ─── The cost budget ─────────────────────────────────────────────────────────

def month_start(now: datetime = None) -> datetime:
    now = now or _now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def next_month_start(now: datetime = None) -> datetime:
    start = month_start(now)
    return start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)


def monthly_budget_nis(client_id: int) -> float:
    if (_client(client_id) or {}).get("billing_exempt") is True:
        return float(_settings(client_id).get("gmail_agent_internal_budget_nis") or 0)
    return float(_pricing()["monthly_cost_budget_ils"])


def month_to_date_cost(client_id: int, now: datetime = None) -> float:
    """This calendar month's (UTC) cost of THIS package for the client: every
    client_costs row whose details.action is FEATURE. Unreadable → raises
    (the guard must not assume zero)."""
    rows = (_db().table("client_costs").select("amount,details,created_at")
            .eq("client_id", int(client_id)).gte("created_at", month_start(now).isoformat())
            .eq("details->>action", FEATURE).limit(5000).execute().data or [])
    return round(sum(float(r.get("amount") or 0) for r in rows), 4)


def require_budget(client_id: int, estimate_nis: float):
    """THE guard, before every paid call. Raises BudgetExhausted when the
    call would take this month past the budget (or the cost can't be read —
    fail closed)."""
    budget = monthly_budget_nis(client_id)
    try:
        spent = month_to_date_cost(client_id)
    except Exception as e:
        print(f"[gmail_agent_plan] month-to-date cost unreadable for client {client_id}: {type(e).__name__}")
        raise BudgetExhausted(budget, budget)
    if spent + max(estimate_nis, 0) > budget:
        raise BudgetExhausted(spent, budget)


def allowance(client_id: int) -> dict:
    """Numbers for the admin; `left` is the plain bucket the client may hear."""
    budget = monthly_budget_nis(client_id)
    try:
        spent = month_to_date_cost(client_id)
    except Exception:
        spent = budget
    remaining = max(budget - spent, 0.0)
    share = remaining / budget if budget else 0.0
    left = ("used_up" if share <= 0.02 else "a_little" if share < 0.25
            else "about_half" if share < 0.6 else "plenty")
    return {"budget_nis": round(budget, 2), "spent_nis": round(spent, 2),
            "remaining_nis": round(remaining, 2), "left": left,
            "resets_on": next_month_start().date().isoformat()}


# ─── Secondary brakes (counts per UTC day) ──────────────────────────────────

def take_places_request(client_id: int, admin_view: bool = False):
    if _count("places_global") > PLACES_GLOBAL_PER_DAY:
        raise CapReached("places_global")
    if not admin_view and _count(f"places:{int(client_id)}") > PLACES_PER_CLIENT_PER_DAY:
        raise CapReached("places_client")


def take_message(client_id: int):
    if _count("outreach_global") > MESSAGES_GLOBAL_PER_DAY:
        raise CapReached("messages_global")
    if _count(f"outreach:{int(client_id)}") > MESSAGES_PER_CLIENT_PER_DAY:
        raise CapReached("messages_client")
