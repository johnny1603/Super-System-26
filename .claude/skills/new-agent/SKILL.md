---
name: new-agent
description: The uallak "agent house blueprint" — required structure for any new or modified agent in agents/ (LLM calls, logging, alerting, error handling). Use when creating an agent, adding an LLM call to an existing one, or editing architect_agent's code-generation prompts.
---

# Building a uallak agent (house blueprint)

Start by copying `agents/_template_agent.py` — it is the canonical, always-current example.
The same rules are enforced on auto-generated agents inside `agents/architect_agent.py`'s
prompts (`_generate_agent_code` and `_generate_test_code`). **If you change a rule, update
all three places: the template, the architect prompts, and this skill.**

## The rules

1. **JSON LLM calls go through `safe_claude_json_call`** (`core/claude_json.py`) — never a
   raw anthropic call plus manual `json.loads`. The helper detects truncation with certainty
   (`stop_reason == "max_tokens"`), retries once at 2× tokens, strips markdown fences, and
   raises `ClaudeJSONError` with the raw response attached. Plain-text calls (like
   `get_reaction`) may use the Anthropic client directly.
2. **Standard logging** — `log_step(AGENT_NAME, step, message)` and
   `timed_step(AGENT_NAME, step, fn)` from `core/agent_base.py`. Format:
   `[ISO timestamp] [AGENT_NAME] step — message`. No ad-hoc print formats.
3. **Alert on human-worthy failures** — `agent_alert(AGENT_NAME, [issues])` from
   `core/agent_base.py`, then return a safe fallback (an empty-but-valid dict). A bad LLM
   response must never crash a client-facing flow.
4. **`AGENT_NAME` module constant** matching the filename; one clear main entry function.
5. **No import-time side effects** — module level holds only imports, constants, and prompt
   strings. `api_server.py` imports agents at startup; a network/DB client created at import
   crashes the whole service if an env var is missing. (Legacy violators: `monitor_agent.py`,
   `client_agent.py` — don't copy them.)
6. **Secrets via `os.environ` / `agents/keys_agent.py` only.** Add new secret names to
   `KEYS` in keys_agent so `validate_keys()` warns at startup.
7. **No local-file persistence** — Cloud Run's filesystem is ephemeral. Durable state goes
   in Supabase. (`data/*.json` usage in master/monitor/architect agents is legacy debt.)
8. **Hebrew for client-facing text; English for code, logs, and prompts.**
9. **Cap output length in the prompt itself** (sentence/item limits) — long responses are
   the main driver of pipeline latency, not just token cost.

## Checklist before shipping an agent

- [ ] Copied structure from `agents/_template_agent.py`
- [ ] Every JSON LLM call uses `safe_claude_json_call` with a `ClaudeJSONError` fallback
- [ ] `log_step`/`timed_step` around each meaningful step
- [ ] `agent_alert` on failure paths a human should see
- [ ] No import-time clients, no hardcoded secrets, no local-file state
- [ ] Prompt states hard output-length limits and "Return JSON only" with the exact shape
- [ ] If wired into the proposal pipeline: does it add a sequential LLM round-trip? Can it
      run in parallel with an existing step instead? (Target: full pipeline < 2 minutes)
