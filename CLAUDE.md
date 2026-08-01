# uallak / Super-System-26

AI-powered autonomous marketing agency for Israeli SMBs. A client goes through a sales chat
(`/chat/`), gets a proposal with 1-2 packages, pays via PayPal (Sandbox only for now), and gets
a session-gated client dashboard (`/dashboard`). Marketing execution agents (real Google/Meta/
TikTok campaign management) do not exist yet — today the system sells; it doesn't yet deliver.

## Critical environment facts (read before running anything)

- This dev machine has **no git, no gcloud, no usable Python** on PATH. Nothing can be run or
  tested locally. Commits happen in GitHub Desktop (user does it), gcloud runs in Cloud Shell
  (user does it). **Use the `deploy` skill before shipping anything.**
- Deployed on Cloud Run: service `super-system`, region `me-west1`, project
  `super-system-500410`. Repo: `johnny1603/Super-System-26` (private).
- The container filesystem (`data/`) is **ephemeral** — wiped every deploy/restart.

## Layout

- `core/api_server.py` — FastAPI app, ALL routes. **The root `/` static mount must stay the
  last registration in the file** or it swallows `/api/*` (caused a real outage once).
- `core/claude_json.py` — `safe_claude_json_call()`: the required helper for every LLM call
  that expects JSON (truncation-aware retry, fence stripping, `ClaudeJSONError`).
  `claude_web_search_call()` is its TEXT-mode sibling for answers that need live web search
  (server-side web_search tool, pause_turn handling, per-search fee tracked in client_costs) —
  search + citations don't mix with strict JSON output, hence two paths. Used by the support
  chat's two-stage flow (JSON call gates → emits web_search_query → text call searches).
- `core/agent_base.py` — `log_step` / `timed_step` / `agent_alert`: standard logging+alerting.
- `core/competitor_research.py` — the shared "look before you generate" step, used by the
  media, website and ads agents through three LENSES (`media` / `website` / `ads`). One
  cached `claude_web_search_call` per (client, lens) — 14/30/7-day TTLs — so four agents
  don't buy the same research four times. **`seo_agent` deliberately does NOT use it**
  (it is tool-first: real SEMrush/Ahrefs data beats a web search); the dependency runs the
  other way — this module reads seo_agent's cached research for real competitor domains at
  zero paid units. Standing rules in every lens prompt: never invent metrics, inform never
  copy, say plainly when nothing was findable.
- `agents/` — one file per agent. `agents/_template_agent.py` is the canonical structure;
  use the `new-agent` skill when creating or modifying agents.
- `agents/onboarding_agent.py` — the sales pipeline (`run_full_onboarding`) AND the `PRICING`
  dict, which is the **single source of truth for all business/pricing rules** (setup-fee
  floors, per-platform 350 NIS monthly fees, SEO budget pyramid, automation tier, benefit
  months). Change business rules there and in its prompts, nowhere else.
- `agents/qa_agent.py` (numeric, no LLM) → `agents/qa_agent_content.py` (merged content +
  master review) run after `build_proposal`.
- `core/lead_tracking.py` — the sales chat's lead lifecycle and traffic-source
  attribution. A `leads` row is opened when someone LANDS on the chat (not when
  a proposal is built), which is what makes source, first-contact time and
  drop-off knowable. Also owns the outbound tagging scheme
  (`GOOGLE_ADS_FINAL_URL_SUFFIX` / `META_URL_TAGS`) that google_ads_agent and
  meta_ads_agent stamp on campaigns they create — read side and write side of
  attribution deliberately live in one file. `dropped_off` is DERIVED at read
  time from `last_activity_at`, never stored. See `HANDOFF-crm-leads.md`.
- `core/lead_volume.py` — per-client lead-volume PRICING TIERS (value-based, gates
  nothing) plus uallak's own Supabase footprint watch. The two are deliberately
  never joined. **`count_client_leads()` is a documented placeholder seam**: no
  table stores leads delivered *to* a client (`leads` is uallak's OWN funnel —
  one row per client, the conversation that sold them), so it approximates with
  Google/Meta ad conversions and always reports `complete: False`. Tier crossings
  alert admin only — nothing bills automatically. See `HANDOFF-lead-volume-tiers.md`.
- `core/paypal_service.py` — **Sandbox base URL hardcoded**; not live.
- `core/drive_service.py` — Google Drive archive for offboarded clients (service account
  via `GOOGLE_SERVICE_ACCOUNT_JSON`, folder `DRIVE_ARCHIVE_FOLDER_ID`). Closure/transfer
  exports the full client record to Drive, then purges the live rows to a PII-stripped
  tombstone; offboarded clients are hard-locked out of login.
- `core/client_leads.py` — the leads a CLIENT receives (`client_leads` table), fed by the
  PUBLIC signed-token endpoint `POST /api/leads/capture/{token}` that a form on the client's
  own site posts to. Read the module docstring before touching it: this is NOT
  `core/lead_tracking.py`'s `leads`. On sites WE build, that form is injected into the
  contact page automatically at provision time (`website_agent.install_lead_capture_form`,
  see the website skill) — the client never handles the token or an embed snippet; the
  manual snippet is now only a labelled fallback for sites we don't manage.
  See `HANDOFF-client-dashboard-nav.md` and `HANDOFF-lead-capture-autowire.md`.
- `core/landing_pages.py` + `core/landing_domains.py` + `agents/landing_page_agent.py` —
  in-code LANDING pages (lightweight, one offer, one form), deliberately NOT the WordPress
  site `website_agent` builds: no shared table, renderer or hosting path. All clients' pages
  are served by ONE route on this app (`/lp/{client_slug}/{page_slug}`) — never a hosting
  project per page. 3 included per client, **enforced server-side**; a 4th is blocked and
  priced by a human, never by code. Content is STRUCTURED and escaped into a fixed template
  — raw HTML is never stored, because these serve from the same origin as the dashboard
  session cookie. See `.claude/skills/landing-pages/SKILL.md`.
- `core/export_service.py` — .xlsx written with stdlib `zipfile` (no openpyxl), a
  print-styled HTML view the browser turns into a PDF (Hebrew needs an embedded font +
  bidi shaping the container lacks), and Google Doc HTML for `drive_service`. Adding an
  exportable dataset means one branch in `api_server._export_dataset`, nothing else.
- `dashboard/` — static HTML pages served by FastAPI mounts: landing `/`, chat `/chat/`,
  terms `/terms/`, login `/login`, client dashboard `/dashboard`, client profile `/profile`
  (picture, external ad-spend transparency report, logout, account closure/transfer),
  admin dashboard `/admin` (ADMIN_PASSWORD login → signed admin_session cookie; separate
  from the X-Admin-Key header that guards server-to-server /api endpoints).

## Conventions

- Model everywhere: `claude-sonnet-4-6` (default in `core/claude_json.py`).
- Client-facing text in Hebrew; code, logs, prompts in English.
- Secrets only via env vars, registered in `agents/keys_agent.py` `KEYS` so startup warns
  when one is missing. Never hardcode credentials (a Gmail app password leaked into git
  history once and had to be rotated).
- Prompts must state hard output-length limits — response length is the main latency driver.
  The full proposal pipeline has a < 2-minute target and is not there yet.
- Supabase tables: `clients`, `client_accounts`, `client_agents`, `client_activity`,
  `client_communications`, `client_suggestions`, `leads`, `lead_messages`,
  `client_lead_volume`, `client_leads`, `landing_pages`, `login_codes`, `alerts`,
  `client_costs`, `app_settings`, `weekly_reports`. **`leads` is uallak's own acquisition funnel,
  not leads delivered to clients** — a frequent and expensive misreading.
  `client_leads` (core/client_leads.py) is the other direction: people who
  contacted a CLIENT. The two never join.
- Schema changes are hand-applied in the Supabase SQL editor. Put the statements
  in `migrations/<date>-<what>.sql` first so the change is reviewable and
  repeatable — idempotent (`add column if not exists`), and say in the file what
  breaks if it hasn't been run yet.

## Known traps

- PayPal `401 invalid_client` after a deploy: check the PayPal developer dashboard first — a
  stale/deleted Sandbox app on PayPal's side caused this once; it wasn't a code bug.
- Many endpoints are `async def` but call long blocking sync code (LLM calls) — one proposal
  build can block the whole event loop. Prefer plain `def` for blocking endpoints.
- Empathy analysis runs ONCE early (intro only) and is deliberately reused — do not add a
  second full-conversation empathy call; that was removed on purpose for speed.
