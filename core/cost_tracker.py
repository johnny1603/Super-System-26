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
    # Client-facing chat turns (core/claude_json.CHAT_TURN_MODEL, 2026-11-01).
    # Thinking tokens are billed as output and are inside output_tokens.
    "claude-opus-5-5": (4.00, 20.00),
    # Classification steps (core/claude_json.CLASSIFY_MODEL) — and every chat
    # turn before 2026-11-01; list price for prompts up to 100K tokens
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


# ─── Attribution (2026-10-18) ────────────────────────────────────────────────
# Every cost has an OWNER: a client_id, a pre-signup lead/session id (moved to
# the client on conversion — link_lead_costs), or INTERNAL (uallak's own work:
# monitors, price checks). record_cost takes the owner explicitly; when the
# call site can't (the two Claude choke points), the cost_owner() context the
# endpoint opened supplies it. A cost with neither is still recorded — as
# "unattributed", with an admin log line — never dropped.
INTERNAL = "internal"
_owner = contextvars.ContextVar("cost_owner", default=None)

# Coarse buckets for later credit metering
METER_CATEGORIES = ("chat", "proposal", "content", "media", "seo", "research", "infra")
_METER_RULES = (
    # (substring of the fine category or action, bucket) — first match wins.
    # gmail_agent first: its LLM drafts run inside chat turns, and they belong
    # to the package's cost budget, not to chat
    ("gmail_agent", "research"), ("places_api", "research"),
    ("chat_", "chat"), ("claude_support", "chat"), ("claude_interview", "chat"),
    ("engagement_login", "chat"), ("sales_chat", "chat"),
    ("proposal", "proposal"), ("upgrade", "proposal"), ("content_qa", "proposal"),
    ("seo", "seo"),
    ("media", "media"), ("avatar", "media"), ("drive", "media"),
    ("landing", "content"), ("website", "content"), ("competitor", "content"),
    ("content", "content"), ("engagement_weekly", "content"), ("google_ads", "content"),
    ("meta_ads", "content"), ("claude_budget", "content"), ("claude_analysis", "content"),
)


def meter_category(category: str, action: str = "") -> str:
    """The metering bucket for a cost row. The action label (what the work was
    FOR) wins over the fine category (which call made it)."""
    for text in (action or "", category or ""):
        for needle, bucket in _METER_RULES:
            if needle in text:
                return bucket
    return "infra"


@contextmanager
def cost_owner(client_id: int = None, lead_id: str = None, internal: bool = False):
    """Attribute every cost recorded inside this block. Open it at the
    request/job boundary (a sales-chat endpoint: lead_id; a scheduler job:
    internal=True); an explicit client_id at the call site still wins."""
    owner = ({"client_id": int(client_id)} if client_id
             else {"lead_id": str(lead_id)} if lead_id
             else {"internal": True} if internal else None)
    token = _owner.set(owner)
    try:
        yield owner
    finally:
        _owner.reset(token)


_warned_missing_columns = False


def record_cost(category: str, amount_ils: float, client_id, details: dict = None,
                usd: float = None, cost_category: str = None):
    """Fire-and-forget: cost tracking must never break the operation it's
    tracking. `client_id` is REQUIRED: an int, INTERNAL, or None only when a
    cost_owner() context supplies the owner (a lead/session before signup).
    The current cost_action label and a USD figure are added to details
    (explicit keys in `details` win)."""
    global _warned_missing_columns
    try:
        enriched = {"usd": round(usd if usd is not None else (amount_ils or 0) / USD_TO_ILS, 6)}
        label = _action.get()
        if label:
            enriched.update({"action": label["action"], "action_ref": label["ref"]})
        enriched.update(details or {})

        owner = _owner.get() or {}
        row_client, lead_id, attribution = None, None, "unattributed"
        if client_id == INTERNAL:
            attribution = "internal"
        elif client_id:
            row_client, attribution = int(client_id), "client"
        elif owner.get("client_id"):
            row_client, attribution = owner["client_id"], "client"
        elif owner.get("lead_id"):
            lead_id, attribution = owner["lead_id"], "lead"
        elif owner.get("internal"):
            attribution = "internal"
        else:
            print(f"[cost_tracker] ADMIN: unattributed cost '{category}' "
                  f"(action {enriched.get('action', '-')}) — no client, lead or internal owner")
        bucket = cost_category if cost_category in METER_CATEGORIES else \
            meter_category(category, enriched.get("action", ""))
        if row_client:
            from core.deletion_guard import is_deleting
            if is_deleting(row_client):
                # A cost still landing while (or after) the client is deleted:
                # kept so totals don't move, but anonymous from the start — the
                # same shape client_deletion folds the client's history into
                row_client, attribution = None, "deleted_client"
                enriched = {"usd": enriched.get("usd"), "deleted_client": True, "cost_category": bucket}

        base = {"client_id": row_client, "category": category, "amount": amount_ils, "details": enriched}
        try:
            _db().table("client_costs").insert({**base, "lead_id": lead_id, "attribution": attribution,
                                                "cost_category": bucket}).execute()
        except Exception as e:
            # migrations/2026-10-18-cost-attribution.sql not run yet: keep the
            # cost (attribution rides in details), say so once
            if not _warned_missing_columns:
                _warned_missing_columns = True
                print(f"[cost_tracker] ADMIN: attribution columns missing ({e}) — run "
                      f"migrations/2026-10-18-cost-attribution.sql")
            enriched.update({"lead_id": lead_id, "attribution": attribution, "cost_category": bucket})
            _db().table("client_costs").insert(base).execute()
    except Exception as e:
        print(f"[cost_tracker] could not record cost (non-fatal): {e}")


def record_claude_response(response, category: str, client_id, model: str = None):
    """For the few calls that use the SDK directly instead of the
    claude_json choke points: records the response's token cost. Never raises."""
    try:
        usage = getattr(response, "usage", None)
        tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
        tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
        model = model or getattr(response, "model", None)
        record_cost(category, claude_cost_ils(tokens_in, tokens_out, model), client_id,
                    {"input_tokens": tokens_in, "output_tokens": tokens_out, "model": model},
                    usd=claude_cost_usd(tokens_in, tokens_out, model))
    except Exception as e:
        print(f"[cost_tracker] could not record direct Claude call (non-fatal): {e}")


def link_lead_costs(lead_id: str, client_id: int) -> int:
    """On conversion: the sales-chat costs recorded against this lead become
    the new client's. Never raises; returns how many rows moved."""
    if not (lead_id and client_id):
        return 0
    try:
        result = (_db().table("client_costs").update({"client_id": int(client_id), "attribution": "client"})
                  .eq("lead_id", lead_id).is_("client_id", "null").execute())
        return len(result.data or [])
    except Exception as e:
        print(f"[cost_tracker] could not link lead {lead_id} costs to client {client_id}: {e}")
        return 0


def record_client_paid_media(client_id: int, usd_estimate: float, credits=None,
                             kind: str = "image", model: str = "", exempt: bool = None,
                             tool: str = "higgsfield"):
    """One job on one of the client's own creative-tool keys (Higgsfield,
    HeyGen, ElevenLabs — `tool`, so cost stays per tool), as an ESTIMATE (see
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
                 "kind": kind, "model": model, "tool": tool},
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


def costs_by_client_month(months: int = 6) -> dict:
    """Admin view: OUR cost per owner per calendar month, split by metering
    category. Owners are client ids plus three pseudo-owners: "internal",
    "lead" (pre-signup chats that never converted) and "unattributed".
    Client-paid media (a paying client's own Higgsfield) is excluded — it is
    not our cost. Rows from before the attribution migration are bucketed
    from their fine category."""
    now = datetime.now(timezone.utc)
    y, m = now.year, now.month - (months - 1)
    while m <= 0:
        y, m = y - 1, m + 12
    since = datetime(y, m, 1, tzinfo=timezone.utc).isoformat()
    cols = "client_id, category, amount, details, created_at"
    try:
        rows = (_db().table("client_costs").select(cols + ", cost_category, attribution")
                .gte("created_at", since).limit(50000).execute().data or [])
    except Exception:
        rows = (_db().table("client_costs").select(cols)
                .gte("created_at", since).limit(50000).execute().data or [])

    table = {}
    for row in rows:
        details = row.get("details") or {}
        if row.get("category") == CLIENT_PAID_MEDIA and details.get("payer") == "client":
            continue
        attribution = row.get("attribution") or details.get("attribution") or ""
        owner = (str(row["client_id"]) if row.get("client_id")
                 else "internal" if attribution == "internal"
                 # anonymous aggregates of a deleted client (core/client_deletion)
                 else "deleted_client" if attribution == "deleted_client" or details.get("deleted_client")
                 else "lead" if attribution == "lead" or details.get("lead_id")
                 else "unattributed")
        month = (row.get("created_at") or "")[:7]
        bucket = (row.get("cost_category") or details.get("cost_category")
                  or meter_category(row.get("category"), details.get("action", "")))
        cell = table.setdefault(owner, {}).setdefault(month, {c: 0.0 for c in METER_CATEGORIES})
        cell[bucket] = cell.get(bucket, 0.0) + _row_usd(row)

    out = []
    for owner, by_month in table.items():
        for month, cats in sorted(by_month.items(), reverse=True):
            total = sum(cats.values())
            out.append({"owner": owner, "month": month,
                        "by_category_usd": {k: round(v, 4) for k, v in cats.items()},
                        "total_usd": round(total, 4), "total_ils": round(total * USD_TO_ILS, 2)})
    out.sort(key=lambda r: (r["month"], -r["total_usd"]), reverse=False)
    out.sort(key=lambda r: r["month"], reverse=True)
    return {"since": since[:10], "categories": list(METER_CATEGORIES),
            "usd_to_ils": USD_TO_ILS, "rows": out, "rows_read": len(rows)}


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
