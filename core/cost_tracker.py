"""Records the internal cost of AI operations against clients (client_costs
table), so the admin dashboard can show real margin numbers instead of guesses.

v1 coverage: Claude API calls only, priced from actual token usage inside
safe_claude_json_call (the single choke point every JSON LLM call goes
through). Future costly operations (image/video generation, SEO tools) should
call record_cost() with their own category when those agents get built.

All figures are approximations for internal margin tracking - label them
"משוער" anywhere client- or admin-facing.
"""
import os

# claude-sonnet-4-6 list pricing (USD per million tokens) and a fixed
# conversion rate. Good enough for margin tracking; not an accounting system.
CLAUDE_INPUT_USD_PER_MTOK = 3.00
CLAUDE_OUTPUT_USD_PER_MTOK = 15.00
# Anthropic web search tool list pricing - a per-search fee on top of the
# token costs (which the caller tracks separately via claude_cost_ils)
WEB_SEARCH_USD_PER_1000 = 10.00
USD_TO_ILS = 3.40

# Created lazily - no DB client at import time
_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        from supabase import create_client
        _db_instance = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    return _db_instance


def claude_cost_ils(input_tokens: int, output_tokens: int) -> float:
    usd = (input_tokens * CLAUDE_INPUT_USD_PER_MTOK + output_tokens * CLAUDE_OUTPUT_USD_PER_MTOK) / 1_000_000
    return round(usd * USD_TO_ILS, 4)


def web_search_cost_ils(searches: int) -> float:
    return round(searches * (WEB_SEARCH_USD_PER_1000 / 1000) * USD_TO_ILS, 4)


def usd_to_ils(usd: float) -> float:
    """Generic fixed-rate conversion for any future flat-USD-priced operation.
    (Media generation no longer uses it - clients pay Higgsfield directly on
    their own plans, so generation never enters client_costs.)"""
    return round(usd * USD_TO_ILS, 4)


def record_cost(category: str, amount_ils: float, client_id: int = None, details: dict = None):
    """Fire-and-forget: cost tracking must never break the operation it's
    tracking. client_id is nullable - onboarding calls happen before a client
    row exists."""
    try:
        _db().table("client_costs").insert({
            "client_id": client_id,
            "category": category,
            "amount": amount_ils,
            "details": details or {},
        }).execute()
    except Exception as e:
        print(f"[cost_tracker] could not record cost (non-fatal): {e}")
