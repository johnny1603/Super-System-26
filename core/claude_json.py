import json

from anthropic import Anthropic

DEFAULT_MODEL = "claude-sonnet-4-6"

# Anthropic's server-side web search tool (the search runs on their side and
# results come back in the same response - no client-side loop to implement).
# max_uses caps the worst-case per-answer search spend; user_location biases
# results toward what's relevant for Israeli SMBs.
WEB_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 3,
    "user_location": {"type": "approximate", "country": "IL", "timezone": "Asia/Jerusalem"},
}
# Server-side tools can pause mid-turn at their iteration limit
# (stop_reason == "pause_turn"); re-sending the conversation resumes it
MAX_PAUSE_TURN_CONTINUATIONS = 3


class ClaudeJSONError(Exception):
    """Raised when a Claude response can't be turned into JSON, even after a retry."""

    def __init__(self, message, raw_response=None):
        super().__init__(message)
        self.raw_response = raw_response


def _extract_json(raw: str) -> str:
    raw = raw.strip().strip("`").strip()
    if raw.startswith("json"):
        raw = raw[4:].strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end + 1]
    return raw


def safe_claude_json_call(system, user_message, max_tokens=4096, model=DEFAULT_MODEL, api_key=None,
                          client_id=None, cost_category="claude_api"):
    """Call Claude expecting a JSON object back, guarding against the response
    getting cut off mid-JSON.

    - Detects truncation from response.stop_reason == "max_tokens" (a certainty,
      not a guess from a JSON parse failure) and retries once with double max_tokens.
    - Strips markdown fences / stray preamble before parsing, and parses with
      strict=False to tolerate literal newlines inside long text fields.
    - Raises ClaudeJSONError (with the raw response attached) if parsing still
      fails, instead of a cryptic JSONDecodeError.
    - Records the call's token cost to client_costs (this is the single choke
      point for JSON LLM calls, so instrumenting here covers everything).
      Pass client_id / cost_category where attribution is known.
    """
    client = Anthropic(api_key=api_key) if api_key else Anthropic()
    total_input_tokens = total_output_tokens = 0

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    total_input_tokens += response.usage.input_tokens
    total_output_tokens += response.usage.output_tokens

    if response.stop_reason == "max_tokens":
        print(f"[claude_json] response truncated at max_tokens={max_tokens}, retrying with {max_tokens * 2}")
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens * 2,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens

    try:
        from core.cost_tracker import claude_cost_ils, record_cost
        record_cost(
            cost_category,
            claude_cost_ils(total_input_tokens, total_output_tokens),
            client_id=client_id,
            details={"input_tokens": total_input_tokens, "output_tokens": total_output_tokens, "model": model},
        )
    except Exception as e:
        print(f"[claude_json] cost tracking failed (non-fatal): {e}")

    raw = response.content[0].text
    cleaned = _extract_json(raw)

    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError as e:
        raise ClaudeJSONError(
            f"Could not parse JSON from Claude response (stop_reason={response.stop_reason}): {e}",
            raw_response=raw,
        ) from e


def claude_web_search_call(system, user_message, max_tokens=1500, model=DEFAULT_MODEL,
                           client_id=None, cost_category="claude_api_search"):
    """TEXT-mode sibling of safe_claude_json_call, for answers that need live
    web search. Deliberately a SEPARATE code path: search results attach
    citations that split the text into multiple blocks, which doesn't mix with
    strict single-JSON-object output - so JSON-mode agents keep using
    safe_claude_json_call unchanged, and search callers get plain text back.

    - Declares Anthropic's server-side web_search tool; the model decides how
      many searches to run (capped by max_uses).
    - Continues on stop_reason == "pause_turn" (server-tool iteration limit)
      up to MAX_PAUSE_TURN_CONTINUATIONS times.
    - Records BOTH cost components to client_costs: token cost plus the
      per-search fee (usage.server_tool_use.web_search_requests).
    - Returns the response's text blocks joined into one string; raises
      ClaudeJSONError when no text came back (callers already handle it).
    """
    client = Anthropic()
    messages = [{"role": "user", "content": user_message}]
    total_input = total_output = total_searches = 0

    response = None
    for _ in range(MAX_PAUSE_TURN_CONTINUATIONS + 1):
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=[WEB_SEARCH_TOOL],
        )
        total_input += response.usage.input_tokens
        total_output += response.usage.output_tokens
        server_tool_use = getattr(response.usage, "server_tool_use", None)
        total_searches += getattr(server_tool_use, "web_search_requests", 0) or 0
        if response.stop_reason != "pause_turn":
            break
        # Paused mid-turn: append the assistant turn as-is and re-send - the
        # API detects the trailing server-tool block and resumes automatically
        messages = messages + [{"role": "assistant", "content": response.content}]

    try:
        from core.cost_tracker import claude_cost_ils, record_cost, web_search_cost_ils
        record_cost(
            cost_category,
            round(claude_cost_ils(total_input, total_output)
                  + web_search_cost_ils(total_searches), 4),
            client_id=client_id,
            details={"input_tokens": total_input, "output_tokens": total_output,
                     "web_searches": total_searches, "model": model},
        )
    except Exception as e:
        print(f"[claude_json] cost tracking failed (non-fatal): {e}")

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise ClaudeJSONError(
            f"web search call returned no text (stop_reason={response.stop_reason})")
    return text
