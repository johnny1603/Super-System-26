import json

from core.claude_json import ClaudeJSONError, safe_claude_json_call

def qa_check_content(proposal, answers):
    system = """You are a content QA agent for uallak marketing system.
Check the proposal for quality issues and return the corrected version.
Rules:
1. No promises of SEO results before 6 months
2. No unrealistic numbers given the budget
3. Tone must be professional and warm
4. self_help_tips must be business-specific
5. benefit_value must equal monthly_management_total x 2

IMPORTANT: Return ONLY a valid JSON object. No markdown, no backticks, no explanation. Just the JSON."""

    user_message = f"Check and fix this proposal. Return JSON only:\n{json.dumps(proposal)}\n\nClient budget: {answers.get('marketing_budget')}, goal: {answers.get('main_goal')}"

    try:
        corrected = safe_claude_json_call(system, user_message, max_tokens=2000)
        print("QA Agent 2: Content verified")
        return corrected
    except ClaudeJSONError as e:
        print(f"QA Agent 2: Minor issue ({e}), returning original")
        return proposal
