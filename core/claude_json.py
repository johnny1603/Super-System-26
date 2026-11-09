import contextvars
import json
import re
import time
from contextlib import contextmanager

from anthropic import Anthropic

from core.model_routing import TIER_MODELS

DEFAULT_MODEL = "claude-sonnet-4-6"
# Chat-turn models come from ONE table (core/model_routing.TIER_MODELS,
# 2026-11-01). CHAT_TURN_MODEL = the strong tier: the manager, the opening
# interview, the login greeting, feature announcements and the operator are
# locked to it; the specialists' turns are routed per turn
# (model_routing.routed_turn) and land here whenever routing is off.
# Deliverables (proposals, articles, research) stay on DEFAULT_MODEL.
CHAT_TURN_MODEL = TIER_MODELS["heavy"]
CHAT_TURN_EFFORT = "low"          # Opus 5.5 defaults to medium; chat replies are short
# Room for adaptive thinking (always on for Opus 5.5) PLUS the JSON reply —
# billed by what is used, not by this ceiling
CHAT_TURN_MAX_TOKENS = 2000
# The cheap model, ONLY for internal steps where no personality is involved
# (classification / filtering — e.g. question_filter, the turn router). Never
# for text a client reads unless the router chose it for a simple turn.
CLASSIFY_MODEL = TIER_MODELS["simple"]

# Prompt caching (2026-11-01). Every agent's system prompt is passed as
# blocks: the STATIC part (persona, rules, tool definitions — tools render
# before system, so a marker on system covers them too) carries cache_control;
# anything per-client (history, client data, the new message) comes AFTER the
# last marker — in an unmarked system block or in the user message — so it is
# never part of a cached prefix. 5-minute TTL: a client's next turn usually
# comes within minutes, and the shared prompts (one per persona) are reused
# across clients. Minimum cacheable prefix on the 5.5 models is 512 tokens;
# ours are 7-9K.
CACHE_CONTROL = {"type": "ephemeral"}


def cached_system(*static_parts: str, dynamic: str = "") -> list:
    """System blocks: each static part cached (a marker per part, so a shared
    first part — e.g. the concierge rules — is reused whatever follows it),
    then the dynamic part, unmarked. At most 3 static parts (API limit: 4
    breakpoints, one left for a conversation tail)."""
    parts = [p for p in static_parts if p]
    if len(parts) > 3:
        raise ValueError("at most 3 cached system parts")
    blocks = [{"type": "text", "text": p, "cache_control": dict(CACHE_CONTROL)} for p in parts]
    if dynamic:
        blocks.append({"type": "text", "text": dynamic})
    return blocks


# A turn-level usage collector (core/model_routing logs one row per chat turn
# with its tokens and cost): every _record inside collect_usage() also lands
# in the yielded list. Context-local, so concurrent turns never mix.
_usage_sink = contextvars.ContextVar("claude_usage_sink", default=None)


@contextmanager
def collect_usage():
    sink = []
    token = _usage_sink.set(sink)
    try:
        yield sink
    finally:
        _usage_sink.reset(token)


def _collect(model, input_tokens, output_tokens, cache_read, cache_write, ils):
    sink = _usage_sink.get()
    if sink is not None:
        sink.append({"model": model, "input": input_tokens, "output": output_tokens,
                     "cache_read": cache_read, "cache_write": cache_write, "ils": ils})


def _time_call(model, started, response, category, kind):
    """Hand one API call's duration + tokens to the open chat turn
    (core/chat_timing). Never raises — timing must not break a call."""
    try:
        from core import chat_timing
        if chat_timing.current() is None:
            return
        u = usage_tokens(response) if response is not None else {"input": 0, "output": 0,
                                                                  "cache_read": 0, "cache_write": 0}
        chat_timing.note_model_call(model, time.perf_counter() - started, u["input"], u["output"],
                                    u["cache_read"], u["cache_write"], category,
                                    kind if response is not None else kind + "_failed")
    except Exception:
        pass


def usage_tokens(response) -> dict:
    """input = the UNCACHED remainder; cache_read / cache_write separately."""
    usage = getattr(response, "usage", None)
    return {"input": getattr(usage, "input_tokens", 0) or 0,
            "output": getattr(usage, "output_tokens", 0) or 0,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0}


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
# Anthropic's server-side web fetch (2026-11-07, the landing lens): reads a
# page that search returned — its structure, not just a snippet. Capped per
# answer and per page; no per-fetch fee (tokens only).
WEB_FETCH_TOOL = {
    "type": "web_fetch_20260209",
    "name": "web_fetch",
    "max_uses": 3,
    "max_content_tokens": 8000,
}
# Server-side tools can pause mid-turn at their iteration limit
# (stop_reason == "pause_turn"); re-sending the conversation resumes it
MAX_PAUSE_TURN_CONTINUATIONS = 3


# One Anthropic client per API key, reused for the process's lifetime.
# Constructing a client per call meant a fresh connection pool every time, so
# every LLM call in the app paid for a new TCP + TLS handshake to Anthropic
# before it could even send the request. The clients are thread-safe, and
# requests run on FastAPI's threadpool, so sharing them is the intended use.
# Keyed by api_key because callers pass different ones (None = read from env).
_CLIENTS = {}


def _client(api_key=None):
    if api_key not in _CLIENTS:
        _CLIENTS[api_key] = Anthropic(api_key=api_key) if api_key else Anthropic()
    return _CLIENTS[api_key]


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


_TRAILING_COMMA = re.compile(r",\s*([}\]])")
_NO_JSON = object()


def _parse(raw: str):
    """The model's text -> a JSON value, with a LOCAL repair before any paid
    retry: fences/preamble stripped, then the first complete object decoded
    even when prose (with its own braces) follows it, then trailing commas
    dropped. _NO_JSON when none of that yields JSON."""
    cleaned = _extract_json(raw)
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder(strict=False)
    start = raw.find("{")
    if start != -1:
        try:
            return decoder.raw_decode(raw[start:])[0]
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(_TRAILING_COMMA.sub(r"\1", cleaned), strict=False)
    except json.JSONDecodeError:
        return _NO_JSON


# Appended to the user message for the one repair retry (same model)
REPAIR_NOTE = ("\n\n[Your previous answer to this message was not valid JSON. Answer again with ONLY the "
               "JSON object the instructions ask for — no prose, no markdown fences, nothing before or "
               "after it.]")


def _with_repair_note(user_message):
    if isinstance(user_message, list):
        return list(user_message) + [{"type": "text", "text": REPAIR_NOTE.strip()}]
    return f"{user_message}{REPAIR_NOTE}"


def escalation_model(model: str):
    """One tier up per core/model_routing's table, or None at the top. A model
    outside the table (DEFAULT_MODEL, the deliverables' model) counts as the
    standard tier."""
    from core.model_routing import TIER_MODELS as tiers, next_tier
    tier = next((t for t, m in tiers.items() if m == model), "standard")
    up = next_tier(tier)
    return tiers[up] if up else None


def _text(response) -> str:
    """All TEXT blocks, joined. Models with thinking on by default (Haiku 5.5:
    adaptive thinking, display "omitted") return thinking blocks next to the
    text, so content[0] is not always text — and a turn can end (end_turn)
    with thinking blocks and NO text block at all."""
    return "".join(getattr(block, "text", "") or "" for block in (response.content or [])
                   if getattr(block, "type", "") == "text")


def _shape(response) -> str:
    """For logs: stop_reason, usage and block TYPES — never content."""
    usage = usage_tokens(response)
    types = [getattr(b, "type", "?") for b in (response.content or [])]
    return (f"stop_reason={response.stop_reason} blocks={types} "
            f"input_tokens={usage['input']} cache_read={usage['cache_read']} "
            f"cache_write={usage['cache_write']} output_tokens={usage['output']}")


def _record(cost_category, model, input_tokens, output_tokens, client_id,
            cache_read_tokens=0, cache_write_tokens=0, extra_details=None):
    """One cost row per model call, priced with cached vs uncached input
    separated, plus ONE log line per turn (tokens + ILS; never content)."""
    try:
        from core.cost_tracker import claude_cost_ils, claude_cost_usd, record_cost
        ils = claude_cost_ils(input_tokens, output_tokens, model, cache_read_tokens, cache_write_tokens)
        _collect(model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, ils)
        print(f"[claude_cost] {cost_category} model={model} client={client_id} in={input_tokens} "
              f"cache_read={cache_read_tokens} cache_write={cache_write_tokens} out={output_tokens} "
              f"ils={ils}")
        record_cost(
            cost_category, ils, client_id=client_id,
            details={"input_tokens": input_tokens, "output_tokens": output_tokens, "model": model,
                     "cache_read_tokens": cache_read_tokens, "cache_write_tokens": cache_write_tokens,
                     **(extra_details or {})},
            usd=claude_cost_usd(input_tokens, output_tokens, model, cache_read_tokens, cache_write_tokens),
        )
    except Exception as e:
        print(f"[claude_json] cost tracking failed (non-fatal): {e}")


def _complete(client, system, user_message, max_tokens, model, extra, client_id, cost_category):
    """One answer from ONE model: a truncated turn is retried once with double
    max_tokens (a certainty from stop_reason, not a guess from a parse
    failure). Its cost is recorded against that model."""
    def create(tokens):
        # each API call is timed into the open chat turn (core/chat_timing;
        # a call outside a chat turn records nothing)
        started = time.perf_counter()
        response = None
        try:
            response = client.messages.create(model=model, max_tokens=tokens, system=system,
                                              messages=[{"role": "user", "content": user_message}], **extra)
            return response
        finally:
            _time_call(model, started, response, cost_category, "json")

    response = create(max_tokens)
    total = usage_tokens(response)
    if response.stop_reason == "max_tokens":
        print(f"[claude_json] response truncated at max_tokens={max_tokens}, retrying with {max_tokens * 2}")
        response = create(max_tokens * 2)
        total = {k: total[k] + v for k, v in usage_tokens(response).items()}
    _record(cost_category, model, total["input"], total["output"], client_id,
            total["cache_read"], total["cache_write"])
    return response


def safe_claude_json_call(system, user_message, max_tokens=4096, model=DEFAULT_MODEL, api_key=None,
                          client_id=None, cost_category="claude_api", effort=None, retry_empty=True,
                          json_retry=True, timeout=None):
    """Call Claude expecting a JSON object back, guarding against the response
    getting cut off mid-JSON or coming back EMPTY.

    - Detects truncation from response.stop_reason == "max_tokens" (a certainty,
      not a guess from a JSON parse failure) and retries once with double max_tokens.
    - An EMPTY completion (no text at all — e.g. a thinking-only turn that ended
      with end_turn) is retryable, not a parse error: one more try on the same
      model with the same input, then one on DEFAULT_MODEL. Each empty one is
      logged with stop_reason, usage and block types (never content). Only if
      all of them are empty does it raise ClaudeJSONError — which is what sends
      a chat to core/chat_fallback.
    - Strips markdown fences / stray preamble before parsing, and parses with
      strict=False to tolerate literal newlines inside long text fields.
    - Raises ClaudeJSONError (with the raw response attached) if parsing still
      fails, instead of a cryptic JSONDecodeError.
    - Records every call's token cost to client_costs, against the model that
      ran it (this is the single choke point for JSON LLM calls, so
      instrumenting here covers everything). Pass client_id / cost_category
      where attribution is known.
    - retry_empty=False: ONE attempt only — for a routed turn, whose retry is
      one tier UP (core/model_routing), not the same model again.
    - MALFORMED JSON (2026-11-02, after ClaudeJSONError alerts #113-#124): a
      local repair first (no call: first complete object, trailing commas),
      then ONE repair retry on the same model with REPAIR_NOTE, then ONE call
      a tier up (escalation_model, per model_routing's table) — the tier-up
      step is skipped when retry_empty=False, because a routed turn's caller
      (model_routing.routed_turn) escalates itself. json_retry=False: no paid
      retry at all (the turn router: a malformed answer means 'standard').
    - timeout (seconds): a hard limit per HTTP call with ONE transport retry,
      for work that runs under an approved action's deadline (landing copy,
      alt text). Without it the SDK default (10 minutes, 2 retries) applies —
      long enough to outlive an action's whole run (proposal 16, 2026-11-02).
    """
    client = _client(api_key)
    if timeout and hasattr(client, "with_options"):
        client = client.with_options(timeout=float(timeout), max_retries=1)
    # effort is optional: only sent when a caller asks (e.g. "low" for chat turns)
    extra = {"output_config": {"effort": effort}} if effort else {}

    attempts = ([model, model] + ([DEFAULT_MODEL] if model != DEFAULT_MODEL else [])) if retry_empty else [model]
    raw, response = "", None
    for number, attempt_model in enumerate(attempts, start=1):
        response = _complete(client, system, user_message, max_tokens, attempt_model, extra,
                             client_id, cost_category)
        raw = _text(response)
        if raw.strip():
            break
        print(f"[claude_json] EMPTY completion from {attempt_model} "
              f"(attempt {number}/{len(attempts)}, cost_category={cost_category}, "
              f"client_id={client_id}): {_shape(response)}")
    if not raw.strip():
        raise ClaudeJSONError(
            f"Empty completion from Claude after {len(attempts)} attempts "
            f"({' -> '.join(attempts)}; last {_shape(response)})", raw_response="")
    parsed = _parse(raw)
    if parsed is not _NO_JSON:
        return parsed

    # MALFORMED JSON (the local repair already failed): one repair retry on the
    # same model, then — unless the caller owns escalation (a routed turn,
    # retry_empty=False) — one tier up per core/model_routing. Logged by shape,
    # never content.
    repair_models = ([model] if json_retry else []) + \
        ([up] if json_retry and retry_empty and (up := escalation_model(model)) else [])
    for attempt_model in repair_models:
        print(f"[claude_json] MALFORMED JSON (cost_category={cost_category}, client_id={client_id}, "
              f"{_shape(response)}); repair retry on {attempt_model}")
        response = _complete(client, system, _with_repair_note(user_message), max_tokens, attempt_model,
                             extra, client_id, cost_category)
        retry_raw = _text(response)
        parsed = _parse(retry_raw) if retry_raw.strip() else _NO_JSON
        if parsed is not _NO_JSON:
            return parsed
        raw = retry_raw or raw
    raise ClaudeJSONError(
        f"Could not parse JSON from Claude response after {1 + len(repair_models)} attempt(s) "
        f"({' -> '.join([model] + repair_models)}; stop_reason={response.stop_reason})",
        raw_response=raw)


def _sourced_urls(response) -> list:
    """Every URL the server-side search ACTUALLY returned, from one response.

    Two places carry them: the `web_search_tool_result` blocks (the raw result
    list) and the `citations` attached to text blocks. Both are read, because
    a model can cite a result it found without the block shapes being
    guaranteed stable across API versions.

    This exists so a caller can VERIFY a URL the model wrote in its prose
    against what search really found — see competitor_research's EXAMPLES
    lines, where a fabricated link would be handed to a paying client. Every
    lookup is defensive (getattr/duck-typing): a shape change must degrade to
    "no sources found", never raise inside a research call.
    """
    urls = []
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", "") == "web_search_tool_result":
            for item in getattr(block, "content", None) or []:
                url = getattr(item, "url", "")
                if url:
                    urls.append(url)
        for citation in getattr(block, "citations", None) or []:
            url = getattr(citation, "url", "")
            if url:
                urls.append(url)
    return urls


def claude_web_search_call(system, user_message, max_tokens=1500, model=DEFAULT_MODEL,
                           client_id=None, cost_category="claude_api_search",
                           with_sources=False, fetch=False):
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
    - `with_sources=True` returns `(text, sourced_urls)` instead of just the
      text - the URLs search actually returned, across every continuation.
      Default False keeps every existing caller's contract unchanged.
    - `fetch=True` also declares the server-side web_fetch tool (the model
      may open pages that search returned).
    """
    client = _client()
    tools = [WEB_SEARCH_TOOL] + ([WEB_FETCH_TOOL] if fetch else [])
    messages = [{"role": "user", "content": user_message}]
    total_input = total_output = total_searches = total_cache_read = total_cache_write = 0
    sourced = []

    response = None
    for _ in range(MAX_PAUSE_TURN_CONTINUATIONS + 1):
        started, response = time.perf_counter(), None
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                tools=tools,
            )
        finally:
            _time_call(model, started, response, cost_category, "web_search")
        usage = usage_tokens(response)
        total_input += usage["input"]
        total_output += usage["output"]
        total_cache_read += usage["cache_read"]
        total_cache_write += usage["cache_write"]
        server_tool_use = getattr(response.usage, "server_tool_use", None)
        total_searches += getattr(server_tool_use, "web_search_requests", 0) or 0
        # Collected per response, not just from the last one: a paused turn's
        # searches happen in the EARLIER responses, so reading only the final
        # one would lose most of the sources.
        sourced.extend(_sourced_urls(response))
        if response.stop_reason != "pause_turn":
            break
        # Paused mid-turn: append the assistant turn as-is and re-send - the
        # API detects the trailing server-tool block and resumes automatically
        messages = messages + [{"role": "assistant", "content": response.content}]

    try:
        from core.cost_tracker import (claude_cost_ils, claude_cost_usd, record_cost,
                                       web_search_cost_ils, web_search_cost_usd)
        ils = round(claude_cost_ils(total_input, total_output, model, total_cache_read, total_cache_write)
                    + web_search_cost_ils(total_searches), 4)
        _collect(model, total_input, total_output, total_cache_read, total_cache_write, ils)
        print(f"[claude_cost] {cost_category} model={model} client={client_id} in={total_input} "
              f"cache_read={total_cache_read} cache_write={total_cache_write} out={total_output} "
              f"searches={total_searches} ils={ils}")
        record_cost(
            cost_category, ils,
            client_id=client_id,
            details={"input_tokens": total_input, "output_tokens": total_output,
                     "cache_read_tokens": total_cache_read, "cache_write_tokens": total_cache_write,
                     "web_searches": total_searches, "model": model,
                     "web_search_usd": web_search_cost_usd(total_searches)},
            usd=claude_cost_usd(total_input, total_output, model, total_cache_read, total_cache_write)
            + web_search_cost_usd(total_searches),
        )
    except Exception as e:
        print(f"[claude_json] cost tracking failed (non-fatal): {e}")

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        raise ClaudeJSONError(
            f"web search call returned no text (stop_reason={response.stop_reason})")
    if with_sources:
        # De-duplicated, order preserved (most relevant search hits first)
        return text, list(dict.fromkeys(sourced))
    return text
