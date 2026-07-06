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
- `core/agent_base.py` — `log_step` / `timed_step` / `agent_alert`: standard logging+alerting.
- `agents/` — one file per agent. `agents/_template_agent.py` is the canonical structure;
  use the `new-agent` skill when creating or modifying agents.
- `agents/onboarding_agent.py` — the sales pipeline (`run_full_onboarding`) AND the `PRICING`
  dict, which is the **single source of truth for all business/pricing rules** (setup-fee
  floors, per-platform 350 NIS monthly fees, SEO budget pyramid, automation tier, benefit
  months). Change business rules there and in its prompts, nowhere else.
- `agents/qa_agent.py` (numeric, no LLM) → `agents/qa_agent_content.py` (merged content +
  master review) run after `build_proposal`.
- `core/paypal_service.py` — **Sandbox base URL hardcoded**; not live.
- `dashboard/` — static HTML pages served by FastAPI mounts: landing `/`, chat `/chat/`,
  terms `/terms/`, login `/login`, client dashboard `/dashboard`.

## Conventions

- Model everywhere: `claude-sonnet-4-6` (default in `core/claude_json.py`).
- Client-facing text in Hebrew; code, logs, prompts in English.
- Secrets only via env vars, registered in `agents/keys_agent.py` `KEYS` so startup warns
  when one is missing. Never hardcode credentials (a Gmail app password leaked into git
  history once and had to be rotated).
- Prompts must state hard output-length limits — response length is the main latency driver.
  The full proposal pipeline has a < 2-minute target and is not there yet.
- Supabase tables: `clients`, `client_accounts`, `client_agents`, `client_activity`,
  `client_communications`, `leads`, `login_codes`.

## Known traps

- PayPal `401 invalid_client` after a deploy: check the PayPal developer dashboard first — a
  stale/deleted Sandbox app on PayPal's side caused this once; it wasn't a code bug.
- Many endpoints are `async def` but call long blocking sync code (LLM calls) — one proposal
  build can block the whole event loop. Prefer plain `def` for blocking endpoints.
- Empathy analysis runs ONCE early (intro only) and is deliberately reused — do not add a
  second full-conversation empathy call; that was removed on purpose for speed.
