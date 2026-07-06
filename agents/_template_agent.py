"""HOUSE BLUEPRINT TEMPLATE — copy this file to start a new agent.

This file is never imported by anything. It is the canonical example of how a
uallak agent is structured. The same rules are enforced on auto-generated
agents inside architect_agent.py's prompts — if you change a rule here, update
those prompts too (and .claude/skills/new-agent/SKILL.md).

The rules:

1. LLM calls that expect JSON go through safe_claude_json_call — never a raw
   anthropic call plus manual json.loads. The helper detects truncation via
   stop_reason, retries once at double max_tokens, strips markdown fences, and
   raises ClaudeJSONError with the raw response attached.
2. Log every meaningful step with log_step(AGENT_NAME, ...) and wrap slow work
   in timed_step(...) so pipeline timing is comparable across agents.
3. When something fails in a way a human should hear about, call
   agent_alert(AGENT_NAME, [...]) and return a safe fallback — one bad LLM
   response must never crash a client-facing flow.
4. Secrets come only from os.environ / agents.keys_agent — never hardcoded.
5. Client-facing text in Hebrew; code, logs, and prompts in English.
6. Module level holds only imports, constants, and prompt strings — no network
   clients or DB connections created at import time (Cloud Run imports every
   agent at startup via api_server).
7. Never persist state to local files — the container filesystem is ephemeral
   on Cloud Run and wiped on every deploy. Anything that must survive goes in
   Supabase.
"""
import json

from core.agent_base import agent_alert, log_step, timed_step
from core.claude_json import ClaudeJSONError, safe_claude_json_call

AGENT_NAME = "template_agent"

SYSTEM = """You are a <role> for uallak, an Israeli marketing agency.
<What this agent does, its rules, and hard output-length limits — long outputs
directly slow the pipeline down.>

Return JSON only:
{"result": "Hebrew text"}"""

_FALLBACK = {"result": ""}


def run(payload: dict) -> dict:
    """Primary entry point — every agent exposes one clear main callable."""
    log_step(AGENT_NAME, "run", f"payload keys: {list(payload)}")
    user_message = json.dumps(payload, ensure_ascii=False)
    try:
        return timed_step(
            AGENT_NAME, "llm_call",
            lambda: safe_claude_json_call(SYSTEM, user_message, max_tokens=1000),
        )
    except ClaudeJSONError as e:
        agent_alert(AGENT_NAME, [f"run failed: {e}"])
        return _FALLBACK
