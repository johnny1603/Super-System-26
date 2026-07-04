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


def safe_claude_json_call(system, user_message, max_tokens=4096, model=DEFAULT_MODEL, api_key=None):
    """Call Claude expecting a JSON object back, guarding against the response
    getting cut off mid-JSON.

    - Detects truncation from response.stop_reason == "max_tokens" (a certainty,
      not a guess from a JSON parse failure) and retries once with double max_tokens.
    - Strips markdown fences / stray preamble before parsing, and parses with
      strict=False to tolerate literal newlines inside long text fields.
    - Raises ClaudeJSONError (with the raw response attached) if parsing still
      fails, instead of a cryptic JSONDecodeError.
    """
    client = Anthropic(api_key=api_key) if api_key else Anthropic()

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )

    if response.stop_reason == "max_tokens":
        print(f"[claude_json] response truncated at max_tokens={max_tokens}, retrying with {max_tokens * 2}")
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens * 2,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )

    raw = response.content[0].text
    cleaned = _extract_json(raw)

    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError as e:
        raise ClaudeJSONError(
            f"Could not parse JSON from Claude response (stop_reason={response.stop_reason}): {e}",
            raw_response=raw,
        ) from e
