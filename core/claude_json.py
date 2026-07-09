import json

from anthropic import Anthropic

DEFAULT_MODEL = "claude-sonnet-4-6"


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
