"""Aggregations behind the admin dashboard (/admin + /api/admin/*).

Honesty rule (from the admin-dashboard handoff): never invent a number. Where
data genuinely doesn't exist yet (no cost rows, no churn tracking), return
None/empty and let the UI show an honest empty state.

Money derivations piggyback on what checkout actually records today: the
'subscription_created' client_activity row carries monthly_management_total and
setup_fee_total. There is no dedicated billing table yet.
"""
import os
from datetime import datetime, timedelta, timezone

DEFAULT_SETTINGS = {
    "whatsapp_number": "972504493725",
    "alert_email": os.environ.get("ADMIN_EMAIL", ""),
    "low_margin_threshold_pct": 70,
}

# Created lazily - no DB client at import time
_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        from supabase import create_client
        _db_instance = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    return _db_instance


def _month_start(now=None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def _fee_map() -> dict:
    """client_id -> {monthly_fee, setup_fee, subscription_id, checkout_at} from
    the newest subscription_created activity row per client."""
    rows = (
        _db().table("client_activity")
        .select("client_id, created_at, details, result")
        .eq("action_type", "subscription_created")
        .order("created_at", desc=True)
        .limit(1000)
        .execute()
        .data or []
    )
    fees = {}
    for row in rows:
        cid = row["client_id"]
        if cid in fees:
            continue  # newest-first, first row per client wins
        details = row.get("details") or {}
        fees[cid] = {
            "monthly_fee": details.get("monthly_management_total") or 0,
            "setup_fee": details.get("setup_fee_total") or 0,
            "subscription_id": (row.get("result") or {}).get("subscription_id"),
            "checkout_at": row.get("created_at"),
        }
    return fees


def _costs_since(since_iso: str) -> list:
    return (
        _db().table("client_costs")
        .select("client_id, category, amount, created_at")
        .gte("created_at", since_iso)
        .limit(5000)
        .execute()
        .data or []
    )


def get_overview() -> dict:
    now = datetime.now(timezone.utc)
    month_start = _month_start(now)
    clients = _db().table("clients").select("id, name, status, package, created_at").execute().data or []
    fees = _fee_map()
    settings = get_settings()

    active = [c for c in clients if c.get("status") == "active"]
    mrr = sum(fees.get(c["id"], {}).get("monthly_fee", 0) for c in active)

    # Setup fees recorded at checkouts that happened this month.
    # NOTE (flagged in handoff report): setup fees are RECORDED but the PayPal
    # subscription only charges the monthly fee - collection is manual for now.
    new_this_month = [cid for cid, f in fees.items() if (f.get("checkout_at") or "") >= month_start]
    setup_fees_month = sum(fees[cid].get("setup_fee", 0) for cid in new_this_month)

    cost_rows = _costs_since(month_start)
    total_cost = round(sum(r.get("amount", 0) for r in cost_rows), 2)
    cost_by_category = {}
    cost_by_client = {}
    for r in cost_rows:
        cost_by_category[r["category"]] = round(cost_by_category.get(r["category"], 0) + r.get("amount", 0), 2)
        if r.get("client_id") is not None:
            cost_by_client[r["client_id"]] = round(cost_by_client.get(r["client_id"], 0) + r.get("amount", 0), 2)

    revenue_month = mrr + setup_fees_month
    margin = round(revenue_month - total_cost, 2)
    margin_pct = round(margin / revenue_month * 100, 1) if revenue_month > 0 else None

    # 3-month trend: clients that existed by each month's end and are active
    # now, with their current fee - an approximation (no historical snapshots
    # exist), labeled as such in the UI
    trend = []
    for months_back in (2, 1, 0):
        month_ref = now
        for _ in range(months_back):
            month_ref = month_ref.replace(day=1) - timedelta(days=1)
        month_end = month_ref if months_back else now
        month_clients = [c for c in active if (c.get("created_at") or "") <= month_end.isoformat()]
        trend.append({
            "month": month_ref.strftime("%Y-%m"),
            "clients": len(month_clients),
            "mrr": sum(fees.get(c["id"], {}).get("monthly_fee", 0) for c in month_clients),
        })

    # Naive linear projection from the trend - only if there's real growth data
    projection = None
    if len(trend) == 3 and trend[0]["mrr"] > 0:
        monthly_growth = (trend[2]["mrr"] - trend[0]["mrr"]) / 2
        projection = round(trend[2]["mrr"] + monthly_growth * 3)

    threshold = settings.get("low_margin_threshold_pct", 70)
    low_margin_clients = []
    for c in active:
        fee = fees.get(c["id"], {}).get("monthly_fee", 0)
        client_cost = cost_by_client.get(c["id"], 0)
        if fee > 0 and client_cost > 0:
            client_margin_pct = round((fee - client_cost) / fee * 100, 1)
            if client_margin_pct < threshold:
                low_margin_clients.append({
                    "client_id": c["id"], "name": c.get("name", ""),
                    "margin_pct": client_margin_pct, "cost": client_cost, "fee": fee,
                })

    return {
        "mrr": mrr,
        "active_clients": len(active),
        "setup_fees_month": setup_fees_month,
        "new_clients_month": len(new_this_month),
        "cost_month": total_cost,
        "cost_by_category": cost_by_category,
        "margin_month": margin,
        "margin_pct": margin_pct,
        "trend": trend,
        "projection_mrr_3m": projection,
        # No churn tracking exists yet (nothing ever sets a churned status) -
        # honest null, UI shows "אין מעקב עדיין"
        "churn_month": None,
        "low_margin_clients": low_margin_clients,
        "low_margin_threshold_pct": threshold,
    }


def list_clients_admin() -> list:
    clients = _db().table("clients").select("*").order("created_at", desc=True).execute().data or []
    fees = _fee_map()
    accounts = _db().table("client_accounts").select("client_id, platform, status").execute().data or []
    cost_rows = _costs_since(_month_start())

    platforms_by_client = {}
    for a in accounts:
        if a.get("status") == "active":
            platforms_by_client.setdefault(a["client_id"], []).append(a["platform"])

    cost_by_client = {}
    for r in cost_rows:
        if r.get("client_id") is not None:
            cost_by_client[r["client_id"]] = round(cost_by_client.get(r["client_id"], 0) + r.get("amount", 0), 2)

    activity_rows = (
        _db().table("client_activity")
        .select("client_id, created_at")
        .order("created_at", desc=True)
        .limit(1000)
        .execute()
        .data or []
    )
    last_activity = {}
    for row in activity_rows:
        last_activity.setdefault(row["client_id"], row["created_at"])

    result = []
    for c in clients:
        fee = fees.get(c["id"], {}).get("monthly_fee", 0)
        cost = cost_by_client.get(c["id"], 0)
        margin_pct = round((fee - cost) / fee * 100, 1) if fee > 0 and cost > 0 else None
        result.append({
            "id": c["id"],
            "name": c.get("name", ""),
            "email": c.get("email", ""),
            "phone": c.get("phone", ""),
            "package": c.get("package", ""),
            "status": c.get("status", ""),
            "platforms": platforms_by_client.get(c["id"], []),
            "last_activity": last_activity.get(c["id"]),
            "monthly_fee": fee,
            "cost_month": cost,
            "margin_pct": margin_pct,
        })
    return result


def get_client_admin(client_id: int) -> dict:
    from agents.client_agent import get_client, get_accounts, get_activity

    client = get_client(client_id)
    if not client:
        return {}
    fees = _fee_map().get(client_id, {})

    next_billing = None
    if fees.get("subscription_id"):
        try:
            from core.paypal_service import get_subscription_status
            next_billing = get_subscription_status(fees["subscription_id"]).get("next_billing_time")
        except Exception as e:
            print(f"[admin_service] paypal status fetch failed for client {client_id}: {e}")

    cost_rows = [r for r in _costs_since(_month_start()) if r.get("client_id") == client_id]
    cost_by_category = {}
    for r in cost_rows:
        cost_by_category[r["category"]] = round(cost_by_category.get(r["category"], 0) + r.get("amount", 0), 2)
    total_cost = round(sum(r.get("amount", 0) for r in cost_rows), 2)

    fee = fees.get("monthly_fee", 0)
    google_account = next((a for a in get_accounts(client_id)
                           if a.get("platform") == "google_ads" and a.get("status") == "active"), {})

    return {
        "client": client,
        "monthly_fee": fee,
        "setup_fee": fees.get("setup_fee", 0),
        "subscription_id": fees.get("subscription_id"),
        "next_billing": next_billing,
        "google_ads_customer_id": google_account.get("account_id"),
        "cost_month": total_cost,
        "cost_by_category": cost_by_category,
        "margin_month": round(fee - total_cost, 2) if fee > 0 else None,
        "margin_pct": round((fee - total_cost) / fee * 100, 1) if fee > 0 else None,
        "activity": get_activity(client_id, limit=20),
    }


def list_alerts(status: str = None, limit: int = 100) -> list:
    query = _db().table("alerts").select("*").order("created_at", desc=True).limit(limit)
    if status:
        query = query.eq("status", status)
    return query.execute().data or []


def resolve_alert(alert_id: int) -> dict:
    result = (
        _db().table("alerts")
        .update({"status": "resolved", "resolved_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", alert_id)
        .execute()
    )
    return result.data[0] if result.data else {}


def list_weekly_reports(limit: int = 20) -> list:
    return (
        _db().table("weekly_reports")
        .select("*")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data or []
    )


def get_settings() -> dict:
    rows = _db().table("app_settings").select("*").execute().data or []
    settings = dict(DEFAULT_SETTINGS)
    for row in rows:
        settings[row["key"]] = row["value"]
    return settings


def update_settings(changes: dict) -> dict:
    for key, value in changes.items():
        if key not in DEFAULT_SETTINGS:
            continue  # only known settings are writable
        _db().table("app_settings").upsert(
            {"key": key, "value": value, "updated_at": datetime.now(timezone.utc).isoformat()},
            on_conflict="key",
        ).execute()
    return get_settings()
