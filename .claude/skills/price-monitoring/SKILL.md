---
name: price-monitoring
description: How uallak keeps its third-party vendor pricing constants (Higgsfield, ElevenLabs, HeyGen, SEOptimer, SEMrush, Ahrefs, InstaWP) from going stale — the single-source-of-truth constants file and the bi-monthly automated web-search check that alerts when a live price looks different. Use when touching core/third_party_pricing.py, agents/price_monitor_agent.py, or any pricing number that references a third-party vendor.
---

# Third-party pricing constants + automated monitoring

## The constants file — single source of truth (2026-07-21)

`core/third_party_pricing.py` — `THIRD_PARTY_PRICING`, one entry per vendor:
`vendor`, `what_for` (which agent/feature uses it and whose cost it is —
client-direct vs. ours), `pricing_url`, `checked_at` (date a human last
verified it), `currency`, and either `plans` (a dict of plan-name → monthly
USD price) or `generation_usd_per_min_range` (for pay-as-you-go vendors like
HeyGen with no flat plan).

**Never hardcode one of these numbers a second time anywhere else.**
Current importers:
- `agents/budget_agent.py` — `HEYGEN_USD_PER_MIN_RANGE` and
  `SEO_TOOL_LIST_PRICE_USD_MONTH` are derived from this file at import time
  (kept as module attributes for backward compatibility with existing
  callers — but they're no longer where the numbers actually live).
- `core/admin_service.get_pricing_reference()` — surfaces the whole
  `THIRD_PARTY_PRICING` dict verbatim as `third_party_vendors` in the admin
  dashboard's "מחירון מלא" tab.

If you need a new vendor price anywhere in the codebase, add it here first,
then import it — don't write a new literal dollar figure in a docstring,
comment, or constant.

## Why `checked_at` only moves when a HUMAN updates the number

The automated monitor (below) detects that a price *might* have moved; it
never edits this file itself, and deliberately does NOT bump `checked_at`
just because a check ran. Auto-updating the date would create a dangerous
illusion — "checked recently" reads as "verified current" to anyone glancing
at this file, even on a run that flagged a real change nobody's fixed yet.
`checked_at` means "a person looked at the vendor's page and confirmed these
numbers on this date" — nothing else.

## Automated bi-monthly check (`agents/price_monitor_agent.py`)

For each vendor, ONE `claude_web_search_call` (text-mode — web search and
strict JSON output don't mix, see `core/claude_json.py`) is given the
vendor's pricing page URL and our stored reference, and told to search and
compare. The model's reply must start with exactly one word:

- `MATCH` — current pricing still lines up with what's stored, no action.
- `CHANGED` — a real difference was found (price moved, plan
  renamed/added/removed, or the pricing MODEL itself changed).
- `UNCERTAIN` — search was inconclusive, blocked, or ambiguous.

**Business rule (explicit, not just an implementation detail): false
negatives are worse than false positives.** Anything that isn't a clean
`MATCH` — including a reply that doesn't follow the required format at all —
is treated as worth a human look and included in the `agent_alert`. The
agent never tries to be clever about "is this REALLY different enough to
bother Johnny" — that judgment call belongs to the human reviewing the
alert, not to a parsed heuristic that could quietly suppress a real change.

**No dedup/cooldown** between runs (unlike `budget_agent`'s weekly deviation
alerts, which dedup for 6 days) — at twice-a-month cadence, a repeated alert
for a constant nobody's gotten around to fixing yet is a useful nudge, not
spam. If this ever moves to a tighter cadence, add dedup then.

## Endpoint + scheduler

`GET /api/pricing/monitor-scan` (X-Admin-Key) — runs the check for every
vendor in `THIRD_PARTY_PRICING`, returns `{checked, flagged, results}`.

```
gcloud scheduler jobs create http pricing-monitor --schedule="0 8 1,15 * *" \
  --uri="{SERVICE_URL}/api/pricing/monitor-scan" --http-method=GET --update-headers=X-Admin-Key={ADMIN_KEY}
```

(1st and 15th of each month, 08:00 — add `--time-zone="Asia/Jerusalem"` to
anchor it to Israel time like the other scheduler jobs in this repo.)

## When a CHANGED/UNCERTAIN alert lands

This is a manual step by design — the agent flags, a human decides:

1. Open the vendor's `pricing_url` from `core/third_party_pricing.py` and
   confirm the actual current numbers.
2. Update the vendor's `plans` (or `generation_usd_per_min_range`) AND bump
   `checked_at` to today.
3. Check whether the change affects anything downstream: `PRICING["avatar"]`
   `client_direct_costs_note_he`, the avatar/media/website skill docs'
   prose mentions of the old numbers, or `budget_agent`'s estimate ranges.

## Gotchas

- Every check is independent — one vendor's search failing (rate limit,
  page down, model error) never blocks the rest; it just becomes an
  `UNCERTAIN` result for that vendor alone.
- The web-search call costs money per vendor per run (Anthropic's per-search
  fee + tokens, tracked via `cost_category="claude_price_monitor"` in
  `client_costs`, `client_id=None` since this isn't client-specific) — 7
  vendors × 2 runs/month is a small, predictable cost; don't raise the
  cadence without considering that this scales linearly.
- `THIRD_PARTY_PRICING`'s `instawp` entry is the one exception to "these are
  all client-direct costs" — it's OUR internal hosting cost basis for
  Phase-2 provisioned sites (feeds `PRICING["website"]["new_site_hosting"]`
  and `budget_agent`'s `internal_cost_gaps`), not something a client pays.
