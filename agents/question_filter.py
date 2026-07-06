"""question_filter agent — decides which base chat questions were already answered
in the client's free-text intro, so the chat can skip them instead of asking twice.

The chat page holds the question list client-side and does the actual filtering;
this agent only returns the IDs to skip.
"""
from core.agent_base import log_step
from core.claude_json import ClaudeJSONError, safe_claude_json_call

AGENT_NAME = "question_filter"

BASE_QUESTION_IDS = [
    "business_age",
    "financial_status",
    "marketing_budget",
    "existing_digital",
    "main_goal",
    "biggest_fear",
]

SYSTEM = """You are a smart question filter for an onboarding chatbot.
The client already wrote an opening message describing their business.
Your job is to identify which follow-up questions are already answered in that opening message.

Question IDs and what answers them:
- business_age — how long the business has existed
- financial_status — the financial situation (profit/loss)
- marketing_budget — their monthly marketing budget
- existing_digital — their existing digital presence (website, social accounts)
- main_goal — their main goal for the coming months
- biggest_fear — their biggest fear/hesitation about trying a marketing service

Rules:
- Only include an ID if you are VERY confident the answer is clearly present
- When in doubt, keep the question (do not include its ID)

Return JSON only:
{"skip_ids": ["id1", "id2"]}"""


def get_skip_ids(intro: str, api_key: str = None) -> list:
    """Return the base-question IDs already answered by the intro. Never raises —
    a filter failure just means no questions get skipped."""
    if not intro:
        return []
    try:
        result = safe_claude_json_call(
            SYSTEM, f"Client opening message: {intro}", max_tokens=200, api_key=api_key
        )
        skip_ids = [i for i in result.get("skip_ids", []) if i in BASE_QUESTION_IDS]
        if skip_ids:
            log_step(AGENT_NAME, "filter", f"skipping {skip_ids}")
        return skip_ids
    except ClaudeJSONError as e:
        log_step(AGENT_NAME, "filter_failed", f"{e} — keeping all questions")
        return []
