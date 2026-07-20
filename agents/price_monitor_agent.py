"""Bi-monthly automated check that our stored third-party vendor pricing
(core/third_party_pricing.py — Higgsfield, ElevenLabs, HeyGen, SEOptimer,
SEMrush, Ahrefs, InstaWP) hasn't quietly gone stale.

For each vendor, one web-search-grounded Claude call compares our stored
reference price(s) against what the vendor's own pricing page shows RIGHT
NOW, and returns a plain-language verdict. Deliberately NOT a JSON call:
Anthropic's server-side web_search tool doesn't mix with strict JSON output
(see claude_web_search_call's docstring) — the model is instructed to start
its reply with one fixed word instead, which is parsed deterministically.

Bar for flagging (explicit business call, not just an implementation
detail): false negatives (missing a real price change) are worse than false
positives (flagging something that didn't actually change) — so ANY result
that isn't a clean, confident "still matches" gets surfaced to Johnny via
agent_alert for a manual look. This agent only ever recommends a look; it
never edits third_party_pricing.py itself.

No dedup/cooldown on repeat alerts (unlike budget_agent's weekly deviation
scan) — at a twice-a-month cadence, a repeated alert for a constant nobody's
updated yet is a useful nudge, not spam.
"""
from core.agent_base import agent_alert, log_step, timed_step
from core.claude_json import ClaudeJSONError, claude_web_search_call
from core.third_party_pricing import THIRD_PARTY_PRICING

AGENT_NAME = "price_monitor_agent"

VERDICTS = ("MATCH", "CHANGED", "UNCERTAIN")

SEARCH_SYSTEM = """You are uallak's pricing-monitoring assistant. You are given ONE third-party
vendor, its official pricing page, and our currently STORED reference price(s) for it (checked on
a specific date). Search the web to find that vendor's CURRENT public pricing, then compare it
against what we have stored.

Your FIRST WORD must be exactly one of: MATCH, CHANGED, UNCERTAIN
- MATCH: the current public pricing you found is the same as (or trivially within rounding of)
  our stored reference — no real change worth a human look.
- CHANGED: the current public pricing is meaningfully different from our stored reference (a
  plan's price moved, a plan was renamed/removed/added, or the pricing MODEL itself changed —
  e.g. flat monthly became pay-as-you-go, or vice versa).
- UNCERTAIN: you could not find clear, current, confident pricing information, or the page/search
  results were ambiguous, contradictory, or unreachable. Err toward UNCERTAIN whenever you're not
  genuinely confident — a missed real change is worse than an unnecessary manual check.

After that first word, on the same line, give ONE short sentence explaining what you found — the
actual current number(s), if you found them — for a human to action. NEVER fabricate a specific
price you didn't actually find in search results; if you're not sure of the exact number, say so
and use UNCERTAIN instead of guessing.

Reply in English, plain text, no JSON, no markdown."""


def _stored_summary(data: dict) -> str:
    plans = data.get("plans")
    if plans:
        return ", ".join(f"{name} ${price}/mo" for name, price in plans.items())
    price_range = data.get("generation_usd_per_min_range")
    if price_range:
        return f"pay-as-you-go ${price_range[0]}-${price_range[1]}/min"
    return "no flat reference stored"


def _check_vendor(key: str, data: dict) -> dict:
    query = (f"Vendor: {data['vendor']}. Official pricing page: {data['pricing_url']}. "
             f"Our stored reference (last checked {data['checked_at']}): {_stored_summary(data)}. "
             f"Find their CURRENT public pricing and compare it against our stored reference.")
    try:
        text = claude_web_search_call(SEARCH_SYSTEM, query, max_tokens=400,
                                      cost_category="claude_price_monitor")
    except ClaudeJSONError as e:
        return {"vendor": key, "verdict": "UNCERTAIN", "detail": f"search call failed: {e}"}

    parts = text.strip().split(None, 1)
    verdict = parts[0].upper().strip(".,:") if parts else ""
    if verdict not in VERDICTS:
        # The model didn't follow the required format - err toward flagging,
        # never toward silently treating an unparseable reply as a match.
        verdict = "UNCERTAIN"
    detail = (parts[1].strip() if len(parts) > 1 else text.strip())[:400]
    return {"vendor": key, "verdict": verdict, "detail": detail}


def run_price_check() -> dict:
    """Cron entry point (GET /api/pricing/monitor-scan): checks every vendor
    in THIRD_PARTY_PRICING and alerts on anything that isn't a clean MATCH.
    One vendor's failure never blocks the rest — each check is independent."""
    results = []
    for key, data in THIRD_PARTY_PRICING.items():
        result = timed_step(AGENT_NAME, f"check_{key}",
                           lambda k=key, d=data: _check_vendor(k, d))
        results.append(result)
        log_step(AGENT_NAME, "check_vendor", f"{key}: {result['verdict']} — {result['detail'][:100]}")

    flagged = [r for r in results if r["verdict"] != "MATCH"]
    if flagged:
        agent_alert(AGENT_NAME, [
            f"{r['vendor']} pricing {r['verdict']} — {r['detail']} "
            f"(review core/third_party_pricing.py['{r['vendor']}'])"
            for r in flagged
        ])

    summary = {"checked": len(results), "flagged": len(flagged), "results": results}
    log_step(AGENT_NAME, "run_price_check", f"done — {summary['checked']} checked, {summary['flagged']} flagged")
    return summary
