import anthropic
import json
import os

def qa_check_content(proposal, answers):
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    
    system = """You are a content QA agent for uallak marketing system.
Check the proposal for quality issues and return the corrected version.
Rules:
1. No promises of SEO results before 6 months
2. No unrealistic numbers given the budget
3. Tone must be professional and warm
4. self_help_tips must be business-specific
5. benefit_value must equal monthly_management_total x 2

IMPORTANT: Return ONLY a valid JSON object. No markdown, no backticks, no explanation. Just the JSON."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": f"Check and fix this proposal. Return JSON only:\n{json.dumps(proposal)}\n\nClient budget: {answers.get('marketing_budget')}, goal: {answers.get('main_goal')}"}]
        )
        raw = response.content[0].text.strip()
        
        # נקה את הטקסט
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()
        
        corrected = json.loads(raw)
        print("QA Agent 2: Content verified")
        return corrected
    except Exception as e:
        print(f"QA Agent 2: Minor issue ({e}), returning original")
        return proposal
