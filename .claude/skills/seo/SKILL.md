---
name: seo
description: How uallak's organic SEO agent works — client-paid research tools (SEOptimer/SEMrush/Ahrefs by PRICING tier), free own-site audits via the WordPress connection, strategy that routes to Johnny (not the client), article writing with anti-scaled-content iron rules, and the backlinks-are-human-owned boundary. Use when touching agents/seo_agent.py, core/seo_tools_service.py, or any /api/seo* endpoint.
---

# Organic SEO agent

## Division of labor (don't blur it)

- **website_agent owns the site**: publishing, editing, standards, plugins.
  Articles from seo_agent go THROUGH `website_agent.publish_content` as
  **drafts** — the human sign-off is publishing in WP.
- **seo_agent owns strategy + research + article generation.** It never
  touches WP directly for writes.
- **core/seo_tools_service.py** is HTTP-only (mirrors the other services):
  adapters for the client-paid research tools.
- **Future media agent** plugs in at
  `seo_agent.get_recent_articles_for_promotion(client_id)` (published
  articles → social adaptation with links back). Don't build it a parallel path.

## The two approval lanes (business decision, 2026-07)

- **Organic STRATEGY → Johnny, never the client**: `build_strategy` logs a
  `seo_strategy_proposed` activity row (full plan in `details.plan`) and
  fires `agent_alert` → the admin dashboard's alerts list is the review
  inbox. Clients can't evaluate SEO strategy; do NOT route this through
  `client_suggestions`.
- **Routine CONTENT → the normal draft flow**: articles land as WP drafts;
  approval = a human publishing them in WordPress.

**Approving a strategy now writes its articles automatically (2026-07-21,
closed the manual-trigger gap)** — `approve_strategy(client_id)`
(`POST /api/seo/approve-strategy`, X-Admin-Key, or the admin dashboard
drawer's "אשר" button, session-gated via `/api/admin/clients/{id}/seo/*`)
logs a `seo_strategy_approved` activity row and immediately calls
`write_article` for as many `content_plan` topics as this week's
`MAX_ARTICLES_PER_WEEK` cap allows (highest-`priority` first). Remaining
topics are NOT written all at once — `run_seo_cycle` (the same weekly
scheduled job) advances the backlog automatically each week
(`_advance_pending_articles`) until the whole approved plan is written.
Johnny approves once; no more per-topic `write-article` calls. The
`content_plan` can list more topics than one week's cap allows on purpose —
that's expected pacing, not a bug. `write-article` itself stays available
for ad-hoc/manual articles outside a strategy.

## Iron rules (content — mirror of website_agent's standing rules)

Google penalizes **scaled low-quality content**, not AI authorship. Quality +
human review is the compliance strategy. Never build any "make AI content
undetectable" mechanism. Machine-enforced:

- `MAX_ARTICLES_PER_WEEK = 2` per client, checked in code against
  `seo_article_generated` activity rows. Raising it is a business decision.
- Topic dedup: exact-topic repeats within 60 days are refused; recent titles
  are passed to the prompt.
- Every article passes `website_agent.content_quality_issues` (h2-start
  hierarchy, excerpt required, etc.) with ONE repair round; a second failure
  alerts and aborts — bad content never reaches a site.
- Prompts forbid invented facts/prices/testimonials and demand
  business-specific substance (from the sales-chat lead context).
- v1 articles contain NO `<img>` tags (skips the alt/WebP pipeline; images
  are a future media-agent concern).

## Backlinks — human-owned, hard boundary

The agent only IDENTIFIES opportunities (`backlink_opportunities` in the
strategy output, from tool data or Claude research) and surfaces them to
Johnny. It must NEVER automate outreach, directory submission, link buying,
or acquisition of any kind. Johnny does that personally.

## Research: two cost tiers

1. **Client's own site — FREE**: `audit_site` reuses the WordPress connection
   (`wp.list_content_for_audit`, added for this) — inventory, thin/stale
   content, missing excerpts, posting cadence. 1h in-memory cache.
2. **Market/competitors — the CLIENT-PAID tool** (PRICING seo_tiers:
   SEOptimer level A / SEMrush level B / Ahrefs level C):
   - Key stored by ADMIN via `POST /api/seo/connect-tool` → `client_accounts`
     row `platform='seo_tool'`, `account_id`=tool slug, `access_token`=API key
     (no OAuth exists for these tools).
   - Adapters implemented: **SEMrush** (analytics CSV API, database `il`) and
     **Ahrefs** (API v3, Bearer). **SEOptimer has NO adapter yet** — its API
     docs sit behind the white-label add-on paywall; wire it when the first
     Level-A key exists. Until then that tier uses the fallback.
   - **Fallback = Claude + web search** (`claude_web_search_call`, the
     market_reality pattern) when no tool is connected/implemented or the tool
     bundle returns nothing. Never a new paid data source.
   - Research is cached **7 days** in `seo_research_completed` activity rows —
     both paths cost real money (client tool units / our web-search fee).
     `force_refresh=true` burns a fresh run.
   - `seo_tools_service` has a daily call cap (200/day, `api_call_counters`,
     fails open) as a runaway brake.

## Who gets the agent

Assignment = a `client_agents` row (`agent_name='seo_agent'`,
`status='active'`) — admin assigns via the existing
`POST /api/clients/{id}/agents`. The weekly cycle iterates those rows.

## Endpoints (all X-Admin-Key)

- `POST /api/seo/connect-tool` {client_id, tool, api_key}
- `GET /api/seo/audit?client_id=` (free)
- `GET /api/seo/research?client_id=&force_refresh=` (costs money)
- `POST /api/seo/strategy` {client_id} (audit+research+LLM → Johnny alert)
- `POST /api/seo/approve-strategy` {client_id} (approves + writes queued
  articles up to this week's cap immediately; `run_seo_cycle` advances the
  rest weekly)
- `POST /api/seo/write-article` {client_id, topic, target_keyword, notes}
  (manual/ad-hoc — approved-strategy topics write themselves now)
- `GET /api/seo/promotable?client_id=` (media-agent preview)
- `GET /api/seo/cycle` (scheduler — also advances approved strategies now)

Admin dashboard (session cookie, not X-Admin-Key): `GET /api/admin/clients/
{id}/seo/pending-strategy`, `POST /api/admin/clients/{id}/seo/approve-strategy`
— the client drawer's "אסטרטגיית SEO אורגני" section.

Scheduler (weekly, Mondays 08:00 — strategy refresh at most every 21 days per
client, `STRATEGY_MIN_INTERVAL_DAYS`):

```
gcloud scheduler jobs create http seo-cycle --schedule="0 8 * * 1" \
  --uri="{SERVICE_URL}/api/seo/cycle" --http-method=GET --update-headers=X-Admin-Key={ADMIN_KEY}
```

## Explicitly deferred

- **Google Search Console** (real ranking/traffic truth): future extension of
  agents/google_ads_agent.py's OAuth, NOT a new integration. Nothing in
  seo_agent assumes it exists.
- SEOptimer adapter (docs/key needed), strategy approve/reject admin UI,
  article images, writing Yoast/Rank Math meta fields (blocked in
  website_agent too), automated internal-linking edits, rank tracking.

## Gotchas

- **The tool adapters are reading-verified only** — written against public
  docs, never run with a live key. First SEMrush/Ahrefs key: run
  `GET /api/seo/research?client_id=&force_refresh=true` and check the
  `errors` array; column names/params may need one round of fixes.
- SEMrush returns errors as `ERROR nn :: message` with HTTP 200 — the service
  handles it, but remember when debugging raw responses.
- All /api/seo endpoints are plain `def` (blocking httpx + LLM calls).
- Per-client tool keys are plaintext in client_accounts — same accepted MVP
  debt as every other platform token.
