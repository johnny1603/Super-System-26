# uallak — Handoff Document for Codebase Audit & Skill Creation

**Purpose of this document:** full context for a fresh model session (Claude Fable 5) to (1) audit the codebase, (2) create reusable Skills for future development sessions, (3) design/implement a standard "house blueprint" for how agents should be built, and (4) recommend priorities.

---

## 1. What uallak is

An AI-powered autonomous marketing agency for Israeli small/medium businesses. A client goes through a sales chat, gets a proposal with 1-2 packages, pays via PayPal, and gets access to a client dashboard. The system is meant to eventually run marketing (Google/Meta/TikTok ads, content, SEO) largely autonomously per client.

## 2. Tech stack

- **Backend:** FastAPI (Python), deployed on **Google Cloud Run**, region `me-west1` (Tel Aviv), service name `super-system`
- **Database:** Supabase (Postgres)
- **Repo:** GitHub — `johnny1603/Super-System-26` (private)
- **Payments:** PayPal (currently **Sandbox mode only** — not live)
- **AI:** Anthropic Claude API (model: `claude-sonnet-4-6` used throughout all agents)
- **Messaging:** Google Pub/Sub (topics: `agent-events`, `master-inbox`, `alerts`)
- **Scheduling:** Google Cloud Scheduler (twice-daily monitor scans)
- **Local dev environment note:** the user's Windows machine running Claude Code does **not** have `git` or `gcloud` CLI on PATH — GitHub Desktop is used as the commit/push workaround; `gcloud` commands are run separately in Google Cloud Shell (browser), never locally in Claude Code.

## 3. Agents (in `agents/`)

| File | Role |
|---|---|
| `master_agent.py` | Alert logging + history (`alert()`, `_append_alert_history`). Review logic was merged into `qa_agent_content.py`. |
| `monitor_agent.py` | `listen_for_critical_alerts()` (Pub/Sub) + `run_deep_scan()` (runs 2x/day via Cloud Scheduler hitting `/api/monitor/scan`) |
| `architect_agent.py` | `create_new_agent()` (writes new agent files + generates ~30 mocked tests + fixes on failure, up to 3 rounds), `suspend_agent()`, `propose_agent_deletion()` — writes to `data/agents_status.json` / `data/agent_proposals.json` |
| `onboarding_agent.py` | Core sales logic: `get_dynamic_questions()`, `analyze_client()` calls (via empathy_agent), `build_proposal()` (multi-package, budget-pyramid-aware), `handle_objection()`, `run_full_onboarding()` orchestrates the whole flow |
| `empathy_agent.py` | `analyze_client()` — sales intelligence (client_profile, sales_approach, pricing_framing), runs once early (intro) and reused later (NOT re-run — this was a deliberate speed optimization) |
| `qa_agent.py` | Pure numeric consistency check, no LLM call, fast |
| `qa_agent_content.py` | Merged content + master review (`review_and_fix_proposal()`) — checks ~12 criteria (tone, unrealistic promises, service consistency, honest/scarcity separation, organic SEO presence, etc.) and returns a corrected proposal |
| `question_filter.py` | Skips base questions already answered in the client's free-text intro |
| `keys_agent.py` | Centralized env var / credential access (`get_key()`, `inject_all_keys()`, `validate_keys()`) |
| `client_agent.py` | Client CRUD, `get_client_by_email`, login code creation/validation, activity logging |

## 4. Core services (in `core/`)

- `api_server.py` — FastAPI app, all routes
- `email_service.py` — branded transactional emails (proposal report, admin alert, payment confirmation, login code)
- `paypal_service.py` — `create_subscription()`, `_access_token()`/`_headers()` (OAuth to PayPal), `verify_webhook_signature()`
- `session.py` — HMAC-signed session tokens (stdlib only, no new dependency), 30-day expiry
- `claude_json.py` — **shared helper `safe_claude_json_call(system, user_message, max_tokens, api_key)`** used by all agents. Checks `stop_reason == "max_tokens"` to detect truncation with certainty (not guessing from JSON errors), auto-retries once at 2x tokens, robustly extracts JSON (strips fences, finds `{...}`, `strict=False`), raises `ClaudeJSONError` with raw response on failure. **This is the pattern all future agents should use.**

## 5. Frontend pages (in `dashboard/`)

| Path | Served at | Purpose |
|---|---|---|
| `landing/index.html` | `/` | Marketing homepage |
| `onboarding/index.html` | `/chat/` | The sales chat (base questions + dynamic questions + package selection + PayPal checkout flow) |
| `terms/index.html` | `/terms/` | Terms of service |
| `login/index.html` | `/login` | Client login (email → 6-digit code → session cookie) |
| `client/index.html` | `/dashboard` | Client dashboard — fetches real data from `/api/dashboard` (session-gated, no client_id in URL) |

**Routing note:** static mounts for `/`, `/chat`, `/terms`, `/login`, `/dashboard` must be registered in the correct order in `api_server.py` — the root `/` mount must come **last**, otherwise it swallows all other routes including `/api/*` (this caused a real outage earlier — worth double-checking mount order is still correct).

## 6. Supabase tables

`clients`, `client_accounts`, `client_agents`, `client_activity`, `client_communications`, `leads`, `login_codes`

## 7. Business/pricing rules currently encoded in `onboarding_agent.py`'s `PRICING` + prompts

- Setup fee floor: **1,500 NIS** for full packages, **500 NIS** for single-service-only clients (after showing them the fuller option too)
- Monthly management fee: **350 NIS per platform group**, summed if multiple (Meta = FB+IG combined counts as one group; Google separate; TikTok separate — priced independently because it needs more media production work)
- Organic SEO 3-tier budget pyramid (client pays the tool subscription directly, uallak manages it):
  - Under ~3,000 NIS/month → no organic SEO recommended
  - ~3,000–10,000 → **SEOptimer**
  - ~10,000–15,000 → **SEMrush**
  - 15,000+ → **Ahrefs**
  - (Note: SEMrush has an affiliate program; Ahrefs does not — relevant for future revenue-share plans)
- Payment timeline: Month 1 = setup fee only (no management fee that month), Month 2 = management fee free, Month 3+ = full billing
- Scarcity messaging ("1 of 20 businesses this month" for the 2-free-months benefit) belongs **only** in `scarcity_note`, never in `honest_note` (which must stay purely factual)
- Every package must always include an organic SEO line item or an explicit explanation if the client expressed interest but budget is below threshold — this was a real bug found and fixed today (client interest was being silently dropped)

## 8. Known issues / explicit tech debt (not yet fixed)

1. **No OAuth for real platform connections** — dashboard's "Connect Now" buttons (Google Ads, Meta, TikTok, newsletter) are inert placeholders
2. **Login code has no brute-force protection** — 6-digit code, no rate limiting on attempts (flagged by the previous session, intentionally left out as out-of-scope at the time)
3. **Domain `uallak.com` not connected** — blocked because `me-west1` doesn't support `gcloud run domain-mappings` directly; needs either a Load Balancer setup or migrating the Cloud Run service to a region that supports direct domain mapping
4. **No real approval-queue or SEO-progress-timeline data** — these were removed from the client dashboard mockup rather than shown with fake/invented numbers, since there's no backing data model yet
5. **Website Agent doesn't exist yet** — no way to actually connect to and edit a client's website (e.g. WordPress REST API integration) — needed before any organic/website-related package promises can be fulfilled in practice
6. **Marketing execution agents don't exist yet** — proposal generation works, but there's no agent yet that actually creates/manages real Google Ads / Meta / TikTok campaigns
7. **"Agent house blueprint" not implemented** — the idea (discussed but not built): a standard template all future agents follow — consistent `safe_claude_json_call` usage, consistent logging format (timestamps, step names), consistent `alert()` usage on failure. This is one of the explicit asks below.
8. **PayPal is Sandbox-only** — not yet switched to Live credentials for real payments
9. Today's session hit a real PayPal 401 regression that turned out to be caused by a **stale/deleted Sandbox app on PayPal's own side** (not a code bug) — worth being aware that "401 invalid_client" from PayPal doesn't always mean a code regression; check the PayPal dashboard app status first
10. **Speed is improved but still not fully under the 2-minute target** on the full proposal pipeline (empathy + build_proposal + merged review) — noted as "better, not there yet" in final testing tonight. Further trimming ideas: reduce package count/field verbosity further, or reconsider whether any remaining step can run concurrently.
11. **Automations are never discussed in the proposal or asked about in the chat** — `PRICING` has an automation setup tier (500 NIS for 1-2 basic automations) and the morning's design discussed an "excitement message" about automations for self-managed clients with budget above the entry tier, but there's no dedicated base question asking whether the client wants/needs automations (e.g. instant lead-response bot), and `build_proposal` doesn't appear to include automation as a recommended line item by default. Found in final live testing tonight — needs a question added to the chat flow and/or explicit prompt logic in `build_proposal`.

## 9. What we're asking Fable 5 to do

1. **Full codebase audit** — consistency, dead code, potential bugs, security gaps, anything that looks fragile given the rapid iteration pace of today's build session
2. **Create new Skills** (in the Claude Code skill-creator sense) that would make future development sessions on this project faster/safer — e.g. a skill encoding "how deploys work in this project" (GitHub Desktop → Cloud Shell → gcloud, never local git/gcloud) so future sessions don't rediscover this by trial and error
3. **Design and implement the "agent house blueprint"** described in point 7 above — a concrete, enforced template for how agents should be structured, so `architect_agent.py`'s future auto-generated agents (and any manually-built ones) are consistent by default
4. **General prioritization recommendations** — what should be tackled next, given the state described above

Please read through the actual repository first before making changes, and confirm your understanding of anything ambiguous in this document against what you find in the code itself, since this document was written from memory of today's build session and the code is the source of truth.
