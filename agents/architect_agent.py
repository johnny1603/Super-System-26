import json
import os
import subprocess
import sys
from datetime import datetime

from anthropic import Anthropic

from core.claude_json import safe_claude_json_call

# Created lazily — no network clients at import time (api_server imports this at startup)
_anthropic = None


def _client() -> Anthropic:
    global _anthropic
    if _anthropic is None:
        _anthropic = Anthropic()
    return _anthropic

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS_DIR = os.path.join(BASE_DIR, "agents")
DATA_DIR = os.path.join(BASE_DIR, "data")
AGENTS_STATUS_PATH = os.path.join(DATA_DIR, "agents_status.json")
AGENT_PROPOSALS_PATH = os.path.join(DATA_DIR, "agent_proposals.json")

MAX_FIX_ROUNDS = 3
TEST_TIMEOUT_SECONDS = 30


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _log(msg: str):
    print(f"[{datetime.now().isoformat()}] [ARCHITECT] {msg}")


# ─── Agent status ─────────────────────────────────────────────────────────────

def is_agent_active(agent_name: str) -> bool:
    status = _load_json(AGENTS_STATUS_PATH, {})
    return status.get(agent_name, {}).get("status") != "suspended"


# ─── Code generation ──────────────────────────────────────────────────────────

def _generate_agent_code(need_description: str) -> tuple[str, str]:
    system = """You are an expert Python developer building agents for uallak, an Israeli marketing system.
Write a complete, working Python agent file based on the need description.

The agent MUST follow the uallak house blueprint (the same structure as agents/_template_agent.py):
- Any Claude call that expects JSON back goes through the shared helper — never a raw
  anthropic client call plus manual json.loads:
    from core.claude_json import ClaudeJSONError, safe_claude_json_call
    result = safe_claude_json_call(system_prompt, user_message, max_tokens=1000)
- Define AGENT_NAME = "<the filename>" as a module constant
- Log every meaningful step in the standard format:
    from core.agent_base import log_step, timed_step
    log_step(AGENT_NAME, "step_name", "details")
- On failures a human should hear about, alert and return a safe fallback instead of raising:
    from core.agent_base import agent_alert
    agent_alert(AGENT_NAME, ["what went wrong"])
- One clear main callable function as the primary entry point
- Module level holds only imports, constants, and prompt strings — never create network or
  DB clients at import time
- No hardcoded secrets — read from os.environ
- Never persist state to local files — the production filesystem is ephemeral
- All client-facing strings in Hebrew; code, logs, and prompts in English
- Choose a descriptive snake_case filename ending in _agent

Return JSON only:
{
  "filename": "snake_case_name_without_dot_py",
  "code": "full Python source as a string"
}"""

    result = safe_claude_json_call(
        system, f"Build an agent for this need:\n{need_description}", max_tokens=4000
    )
    return result["filename"], result["code"]


def _generate_test_code(need_description: str, agent_filename: str, agent_code: str) -> str:
    tmp_module = f"_tmp_{agent_filename}"
    system = f"""You are a senior Python test engineer.
Generate exactly 30 test cases for the agent below as a standalone unittest script.

Rules:
- Import the agent as: from agents.{tmp_module} import *
- The agent calls Claude through a helper imported as safe_claude_json_call — mock it with
  unittest.mock.patch("agents.{tmp_module}.safe_claude_json_call") returning plain dicts.
  Also patch("anthropic.Anthropic") for any direct client usage, so tests never make real
  network calls
- Cover: happy path, empty inputs, None inputs, malformed inputs, boundary values,
  wrong types, large inputs, expected return types and keys
- Use self.assert* methods — no bare assert statements
- The script must be runnable standalone: include if __name__ == "__main__": unittest.main()
- Do NOT import pytest

Return only valid Python code. No markdown, no explanation."""

    response = _client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=6000,
        system=system,
        messages=[{"role": "user", "content":
            f"Agent purpose: {need_description}\n\nAgent filename: {agent_filename}.py\n\nAgent code:\n{agent_code}"}]
    )
    return response.content[0].text.replace("```python", "").replace("```", "").strip()


def _run_tests(agent_filename: str, agent_code: str, test_code: str) -> list[str]:
    """Write agent to a temp module in agents/, run the test file, return failure lines."""
    tmp_module_name = f"_tmp_{agent_filename}"
    tmp_agent_path = os.path.join(AGENTS_DIR, f"{tmp_module_name}.py")
    tmp_test_path = os.path.join(DATA_DIR, "_tmp_test.py")

    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(tmp_agent_path, "w", encoding="utf-8") as f:
            f.write(agent_code)
        with open(tmp_test_path, "w", encoding="utf-8") as f:
            f.write(test_code)

        env = os.environ.copy()
        env["PYTHONPATH"] = BASE_DIR + os.pathsep + env.get("PYTHONPATH", "")

        result = subprocess.run(
            [sys.executable, tmp_test_path],
            capture_output=True,
            text=True,
            timeout=TEST_TIMEOUT_SECONDS,
            cwd=BASE_DIR,
            env=env,
        )

        if result.returncode == 0:
            return []

        output = result.stdout + result.stderr
        failures = [
            line.strip() for line in output.splitlines()
            if line.startswith(("FAIL:", "ERROR:")) or "AssertionError" in line
        ]
        return failures if failures else [output[-2000:]]

    finally:
        for path in (tmp_agent_path, tmp_test_path):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


def _fix_agent_code(agent_filename: str, agent_code: str, test_code: str,
                    failures: list[str], need_description: str) -> str:
    system = """You are an expert Python developer fixing a failing agent.
Given the agent code, its test suite, and the specific failures, rewrite the agent code to fix all issues.
Do not modify the test code — only fix the agent.

Return only the corrected Python source code. No markdown, no JSON wrapper."""

    response = _client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=system,
        messages=[{"role": "user", "content":
            f"Agent purpose: {need_description}\n\n"
            f"Agent code:\n{agent_code}\n\n"
            f"Test failures:\n" + "\n".join(failures) + "\n\n"
            f"Test suite (do not change this):\n{test_code}"}]
    )
    return response.content[0].text.replace("```python", "").replace("```", "").strip()


def _generate_summary(need_description: str, agent_filename: str, agent_code: str, rounds: int) -> str:
    system = """Write a concise 3-5 sentence summary of an agent that was just built.
Cover: what it does, its main function signature and return value, and any notable design decisions.
Do NOT include source code. Write in English."""

    response = _client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content":
            f"Need: {need_description}\nFile: {agent_filename}.py\nCode:\n{agent_code}"}]
    )
    return response.content[0].text.strip()


# ─── Public API ───────────────────────────────────────────────────────────────

def create_new_agent(need_description: str) -> dict:
    _log(f"Creating agent for: {need_description[:80]}")

    agent_filename, agent_code = _generate_agent_code(need_description)
    _log(f"Generated code for {agent_filename}.py")

    test_code = _generate_test_code(need_description, agent_filename, agent_code)
    _log("Generated 30 test scenarios")

    failures = []
    for round_num in range(1, MAX_FIX_ROUNDS + 1):
        _log(f"Running tests — round {round_num}/{MAX_FIX_ROUNDS}")
        try:
            failures = _run_tests(agent_filename, agent_code, test_code)
        except subprocess.TimeoutExpired:
            failures = [f"Test run timed out after {TEST_TIMEOUT_SECONDS}s"]

        if not failures:
            agent_path = os.path.join(AGENTS_DIR, f"{agent_filename}.py")
            with open(agent_path, "w", encoding="utf-8") as f:
                f.write(agent_code)
            summary = _generate_summary(need_description, agent_filename, agent_code, round_num)
            _log(f"Success after {round_num} round(s) — wrote {agent_filename}.py")
            return {
                "status": "success",
                "agent_name": agent_filename,
                "agent_path": f"agents/{agent_filename}.py",
                "rounds_needed": round_num,
                "summary": summary,
            }

        _log(f"Round {round_num}: {len(failures)} failure(s)")
        if round_num < MAX_FIX_ROUNDS:
            agent_code = _fix_agent_code(agent_filename, agent_code, test_code, failures, need_description)

    _log(f"Stuck after {MAX_FIX_ROUNDS} rounds")
    return {
        "status": "stuck",
        "agent_name": agent_filename,
        "rounds_needed": MAX_FIX_ROUNDS,
        "remaining_failures": failures,
        "message": f"Could not resolve all test failures after {MAX_FIX_ROUNDS} rounds. Manual review needed.",
    }


def suspend_agent(agent_name: str, reason: str) -> dict:
    status = _load_json(AGENTS_STATUS_PATH, {})
    status[agent_name] = {
        "status": "suspended",
        "reason": reason,
        "suspended_at": datetime.now().isoformat(),
    }
    _save_json(AGENTS_STATUS_PATH, status)
    _log(f"Suspended {agent_name}: {reason}")
    return {"agent_name": agent_name, "status": "suspended", "reason": reason}


def propose_agent_deletion(agent_name: str, reason: str) -> dict:
    proposals = _load_json(AGENT_PROPOSALS_PATH, [])
    proposal = {
        "agent_name": agent_name,
        "action": "delete",
        "reason": reason,
        "proposed_at": datetime.now().isoformat(),
        "status": "pending_review",
    }
    proposals.append(proposal)
    _save_json(AGENT_PROPOSALS_PATH, proposals)
    _log(f"Logged deletion proposal for {agent_name}")
    return proposal
