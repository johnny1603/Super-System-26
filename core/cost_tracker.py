"""Records the internal cost of AI operations against clients (client_costs
table), so the admin dashboard can show real margin numbers instead of guesses.

Coverage: every Claude call (safe_claude_json_call / claude_web_search_call —
the two choke points, tokens + web-search fees), Drive download egress, and
client-paid media generation as an ESTIMATE (below).

All figures are approximations for internal margin tracking - label them
"משוער" anywhere client- or admin-facing.

ACTION LABELS (2026-10-17): every row also carries details.action (what the
work was FOR — "article_draft", "chat_website", ...) and details.action_ref
(one id per user-visible request, so the Control Room can show "this article
cost X" across its several LLM calls). Set with `with cost_action(...)` around
the work; nested calls inherit it through a contextvar, so no call signature
had to change. A BackgroundTasks job runs outside the request's context and
must open its own `cost_action`.

CLIENT-PAID MEDIA (Higgsfield): the client pays Higgsfield on their own key,
so for a PAYING client it is not our cost — the row is written with amount 0
(every existing margin sum stays correct by construction) and the estimate in
details. For a BILLING-EXEMPT account it is Johnny's own subscription, so the
estimate IS the amount (internal cost). Always details.estimate = True:
Higgsfield exposes an estimate endpoint, not a per-job charge.

No cost figure is ever placed in a chat prompt — this module is read only by
admin screens.
"""
import contextvars
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

# List pricing (USD per million tokens), input/output. Unknown models fall
# back to the default model's price — good enough for margin tracking; not an
# accounting system.
MODEL_PRICES_USD_PER_MTOK = {
    "claude-sonnet-4-6": (3.00, 15.00),
    # Ordinary chat turns (core/claude_json.CHAT_TURN_MODEL); list price for
    # prompts up to 100K tokens, which every chat turn is
    "claude-haiku-5-5": (0.10, 0.50),
}
CLAUDE_INPUT_USD_PER_MTOK, CLAUDE_OUTPUT_USD_PER_MTOK = MODEL_PRICES_USD_PER_MTOK["claude-sonnet-4-6"]
# Anthropic web search tool list pricing - a per-search fee on top of the
# token costs (which the caller tracks separately via claude_cost_ils)
WEB_SEARCH_USD_PER_1000 = 10.00
USD_TO_ILS = 3.40

CLIENT_PAID_MEDIA = "client_paid_media_estimate"

# Created lazily - no DB client at import time
_db_instance = None

_action = contextvars.ContextVar("cost_action", default=None)


def _db():
    global _db_instance
    if _db_instance is None:
        from supabase import create_client
        _db_instance = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    return _db_instance


@contextmanager
def cost_action(action: str, ref: str = None):
    """Label every cost recorded inside this block. `ref` groups the rows of
    one request; a fresh one is minted when omitted. Nesting: the innermost
    label wins (an article draft started from a chat turn is "article_draft",
    not "chat_website")."""
    token = _action.set({"action": action, "ref": ref or uuid.uuid4().hex[:12]})
    try:
        yield _action.get()
    finally:
        _action.reset(token)


def current_action() -> dict:
    return dict(_action.get() or {})


def claude_cost_usd(input_tokens: int, output_tokens: int, model: str = None) -> float:
    price_in, price_out = MODEL_PRICES_USD_PER_MTOK.get(
        model or "", (CLAUDE_INPUT_USD_PER_MTOK, CLAUDE_OUTPUT_USD_PER_MTOK))
    return round((input_tokens * price_in + output_tokens * price_out) / 1_000_000, 6)


def claude_cost_ils(input_tokens: int, output_tokens: int, model: str = None) -> float:
    return round(claude_cost_usd(input_tokens, output_tokens, model) * USD_TO_ILS, 4)


def web_search_cost_usd(searches: int) -> float:
    return round(searches * (WEB_SEARCH_USD_PER_1000 / 1000), 6)


def web_search_cost_ils(searches: int) -> float:
    return round(web_search_cost_usd(searches) * USD_TO_ILS, 4)


def usd_to_ils(usd: float) -> float:
    """Generic fixed-rate conversion for any flat-USD-priced operation."""
    return round(usd * USD_TO_ILS, 4)


def record_cost(category: str, amount_ils: float, client_id: int = None, details: dict = None,
                usd: float = None):
    """Fire-and-forget: cost tracking must never break the operation it's
    tracking. client_id is nullable - onboarding calls happen before a client
    row exists. The current cost_action label and a USD figure are added to
    details (explicit keys in `details` win)."""
    try:
        enriched = {"usd": round(usd if usd is not None else (amount_ils or 0) / USD_TO_ILS, 6)}
        label = _action.get()
        if label:
            enriched.update({"action": label["action"], "action_ref": label["ref"]})
        enriched.update(details or {})
        _db().table("client_costs").insert({
            "client_id": client_id,
            "category": category,
            "amount": amount_ils,
            "details": enriched,
        }).execute()
    except Exception as e:
        print(f"[cost_tracker] could not record cost (non-fatal): {e}")


def record_client_paid_media(client_id: int, usd_estimate: float, credits=None,
                             kind: str = "image", model: str = "", exempt: bool = None):
    """One generation on the client's Higgsfield key, as an ESTIMATE (see
    module docstring for why the amount is 0 for paying clients)."""
    if exempt is None:
        try:
            from core.client_settings import is_exempt
            exempt = is_exempt(client_id)
        except Exception:
            exempt = False
    usd_estimate = float(usd_estimate or 0)
    record_cost(CLIENT_PAID_MEDIA, usd_to_ils(usd_estimate) if exempt else 0.0, client_id,
                {"estimate": True, "payer": "uallak" if exempt else "client",
                 "usd_estimate": round(usd_estimate, 6), "credits": credits,
                 "kind": kind, "model": model},
                usd=usd_estimate if exempt else 0.0)


# ─── Reader: the Control Room's per-client cost view (admin only) ─────────────

def _month_start() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def _row_usd(row: dict) -> float:
    details = row.get("details") or {}
    if "usd" in details:
        return float(details.get("usd") or 0)
    return float(row.get("amount") or 0) / USD_TO_ILS  # rows from before USD was stored


def _bucket(rows: list) -> dict:
    total, by_category, by_action = 0.0, {}, {}
    for row in rows:
        usd = _row_usd(row)
        total += usd
        cat = row.get("category") or "other"
        act = (row.get("details") or {}).get("action") or f"({cat})"
        by_category[cat] = by_category.get(cat, 0.0) + usd
        by_action[act] = by_action.get(act, 0.0) + usd

    def money(usd):
        return {"usd": round(usd, 4), "ils": round(usd * USD_TO_ILS, 2)}
    return {"total": money(total),
            "by_category": {k: money(v) for k, v in sorted(by_category.items(), key=lambda i: -i[1])},
            "by_action": {k: money(v) for k, v in sorted(by_action.items(), key=lambda i: -i[1])}}


def client_cost_view(client_id: int, latest_limit: int = 25) -> dict:
    """Month-to-date and all-time cost for ONE client, by category and by
    action, in USD with an estimated ILS, plus the latest actions (grouped by
    action_ref) with the cost of each. Client-paid media for a PAYING client
    is listed separately and never added to our totals."""
    from core.client_settings import is_exempt
    exempt = is_exempt(client_id)
    rows = (_db().table("client_costs").select("id, category, amount, details, created_at")
            .eq("client_id", client_id).order("created_at", desc=True)
            .limit(5000).execute().data or [])
    def paid_by_client(r):
        return (r.get("category") == CLIENT_PAID_MEDIA
                and (r.get("details") or {}).get("payer") == "client")
    ours = [r for r in rows if not paid_by_client(r)]
    client_paid = [r for r in rows if paid_by_client(r)]
    month = _month_start()

    groups, order = {}, []
    for row in ours:
        details = row.get("details") or {}
        ref = details.get("action_ref") or f"row{row.get('id')}"
        if ref not in groups:
            groups[ref] = {"action": details.get("action") or f"({row.get('category')})",
                           "at": row.get("created_at"), "usd": 0.0, "calls": 0,
                           "categories": set(), "estimate": False}
            order.append(ref)
        g = groups[ref]
        g["usd"] += _row_usd(row)
        g["calls"] += 1
        g["categories"].add(row.get("category") or "other")
        g["estimate"] = g["estimate"] or bool(details.get("estimate"))
    latest = [{**groups[ref], "categories": sorted(groups[ref]["categories"]),
               "usd": round(groups[ref]["usd"], 4),
               "ils": round(groups[ref]["usd"] * USD_TO_ILS, 2)}
              for ref in order[:latest_limit]]

    paid_est = sum(float((r.get("details") or {}).get("usd_estimate") or 0) for r in client_paid)
    return {
        "billing_exempt": exempt,
        # Exempt accounts are not customers: everything here is OUR internal
        # cost with no revenue against it
        "label": "internal_cost" if exempt else "cost_of_service",
        "usd_to_ils": USD_TO_ILS,
        "month_to_date": _bucket([r for r in ours if (r.get("created_at") or "") >= month]),
        "all_time": _bucket(ours),
        "latest_actions": latest,
        "client_paid_media": {
            "estimate": True,
            "usd_month_to_date": round(sum(float((r.get("details") or {}).get("usd_estimate") or 0)
                                           for r in client_paid
                                           if (r.get("created_at") or "") >= month), 4),
            "usd_all_time": round(paid_est, 4),
            "jobs": len(client_paid),
        },
        "rows_read": len(rows),
    }
