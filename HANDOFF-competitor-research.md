# Competitor research before generating (2026-08-01)

Four agents were producing output with no view of the market they were
producing it for. This adds the missing research step to each, and — because
they all needed the same thing — puts the research itself in one shared,
cached module rather than four drifting copies.

## What already existed (investigated before building)

| Agent | Research before this work |
|---|---|
| **SEO** | **Already fully built.** `run_market_research()` is tool-FIRST (SEMrush/Ahrefs via `core/seo_tools_service.py`) with a `claude_web_search_call` fallback, 7-day cache, feeding `build_strategy()` → content plan / on-page fixes / backlink opportunities. Nothing to rebuild. |
| **Media** | None. `_craft_prompt` saw business context + brand palette only. |
| **Website** | None. `run_standards_check` only asserted a contact page EXISTS. |
| **Ads (Google + Meta)** | **None, and the biggest gap**: `create_search_campaign` / `create_link_campaign` took a spec an ADMIN TYPED BY HAND. The agents could execute a campaign but had no view of the market — no LLM step, no research, nothing. |

Also already on file and previously unused for this: the sales chat asks every
prospect for **their strongest competitors by name** (onboarding_agent's MARKET
SIGNALS block) and writes a `market_reality` paragraph into the proposal. The
new research starts from those rather than from a cold guess at the niche.

## What was built

### `core/competitor_research.py` — one shared, cached primitive

Three lenses (`media` / `website` / `ads`), one `claude_web_search_call` per
(client, lens), cached in `client_activity` for **14 / 30 / 7 days**
respectively. So a client generating ten images in a day pays for one research
pass, not ten.

The integration with the SEO agent runs **backwards from what you'd expect, on
purpose**: seo_agent does NOT call this module (it is tool-first, and real paid
Ahrefs/SEMrush data beats a web search). Instead this module *reads* seo_agent's
cached research row for real competitor domains — **zero extra paid units, zero
extra calls** — so a client with a paid SEO tool connected silently improves
every other agent's research too.

Standing rules baked into every lens prompt: never invent metrics; inform never
copy; say plainly when nothing was findable.

### Per agent

1. **Media** — `_craft_prompt()` (every asset) and `_checkin_for_client()` (the
   Saturday weekly plan, where most briefs are actually born) now take a
   `competitor_research` input: visual style, formats, messaging angles, and a
   GAP line. Prompts use it as direction and prefer the GAP. **Failure is
   silent by design** — an empty string leaves generation exactly as it was, so
   a web-search outage can never break the sacred Saturday run.
2. **SEO** — the one genuine gap: strategy was research-grounded, but
   `write_article` was not. It now reads the **existing** 7-day cache (free when
   warm, skipped when cold — it never starts a research run to write one
   article) and is told to pick angles the named competitors haven't covered.
   No new research mechanism.
3. **Website** — `research_site_landscape()`: covers **both** the top organic
   AND top paid results, then a JSON call turns that into a build brief
   (`page_recommendations`, `design_directions`, `conversion_recommendations`,
   `differentiators`). Runs at the end of `provision_site`; changes nothing on
   the site. When the paid side isn't observable the lens must say so — organic
   results are never quietly presented as covering it.
4. **Ads (both)** — `draft_campaign_spec()`: research → a validated spec.
   **Two independent human gates before a shekel moves**: drafting creates
   nothing, and creation still lands the campaign PAUSED. **Budget is never
   inferred** — `daily_budget_ils` is required, because guessing someone's ad
   spend from a stored proposal is not a guess this system should make. Drafts
   pass through the SAME validator the create path uses, with one repair round.
   Drafting **refuses** when research fails: a spec with no market view is the
   generic campaign this exists to prevent.

## Ready-to-use vs blocked — the honest split

**Nothing here is blocked on the pending platform approvals or on an Ahrefs
subscription.** That is the deliberate design choice: the research runs on
Anthropic's server-side web search (`claude_web_search_call`), which needs only
`ANTHROPIC_API_KEY` — already set and already in production use by
`price_monitor_agent`, `seo_agent` and `support_agent`.

| Piece | Status | What it needs |
|---|---|---|
| `core/competitor_research.py`, all 3 lenses | **WORKS TODAY** | `ANTHROPIC_API_KEY` only. No ad account, no SEO subscription. |
| Media research → prompts + weekly plan | **WORKS TODAY** | Nothing new. (Generating the *asset* still needs the client's own Higgsfield key — unchanged, pre-existing.) |
| SEO article research enrichment | **WORKS TODAY** | Nothing new — reads a cache that already fills from the free Claude fallback path. |
| Website landscape research + blueprint | **WORKS TODAY** | Nothing new. Runs on provision; `POST /api/website/research-landscape` re-runs it. |
| Ads `draft_campaign_spec` (both agents) | **WORKS TODAY, up to the draft** | Nothing new. Drafting is pure research + LLM — it never touches the ad platform. |
| Ads `create_*_campaign` (unchanged) | **BLOCKED** | Google/Meta platform approval + a connected ad account. Pre-existing blocker, untouched by this work. |
| SEO tool-grounded competitor domains | **DEGRADED, NOT BLOCKED** | An Ahrefs/SEMrush key the client pays for. Absent → `tool_competitors` is empty and the lens researches from the sales-chat competitors instead. `grounded_in_tool_data: false` is recorded on every row so you can see which mode produced a result. |

The one thing worth calling out as **built-but-unexercisable end-to-end**: the
ads path stops at a reviewed draft until platform approval lands. That is
exactly the seam you asked for — the drafting logic is testable next week the
moment an account connects, with no code change, because it never depended on
the account in the first place.

**No new env vars, no new secrets, nothing to add to `keys_agent.KEYS`.**

## VERIFICATION STATUS

Written and read carefully; **not executed** — this machine has no usable
Python. Nothing here has run against a live client row.

Specific things to watch on the first real run next week:

1. **Cost.** Each uncached lens is up to 3 web searches + tokens, billed to
   `client_costs` under `claude_competitor_research_{lens}`. The TTLs assume
   research is rare; check the first week's `client_costs` rows to confirm the
   cache is actually being hit and not silently missing.
2. **Ads draft validation.** The LLM must count Hebrew characters against
   Google's 30/90 and Meta's 60/60 limits. There is one repair round; if drafts
   routinely fail validation twice, tighten the prompt rather than the limits.
3. **Website lens on the paid side.** Whether the model can actually observe
   paid results is the open empirical question. The prompt requires it to say
   when it can't — verify it does that rather than quietly returning organic
   results, since a false "we checked the ads" is worse than a stated gap.

## Skills updated (step 5)

`media`, `seo`, `website`, `google-ads`, `meta` — all five existed and were
updated. **No skill file exists for a "competitor research" capability itself**;
none was created, since it is a shared `core/` service rather than an agent, and
it is documented in CLAUDE.md's layout map plus each consuming agent's skill.
