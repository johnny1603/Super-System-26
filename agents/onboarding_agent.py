import json
import os
from concurrent.futures import ThreadPoolExecutor

from core.claude_json import safe_claude_json_call

PRICING = {
    "google": {"setup": 300, "monthly_management": 250, "high_budget_pct": 0.05, "high_budget_threshold": 15000},
    "meta": {"setup": 0, "monthly_management": 250},
    "social": {"setup": 700, "monthly_management": 250},
    "seo": {"setup": 0, "monthly_management": 250},
    "email": {"setup": 0, "monthly_management": 50},
    "automation": {"setup": 500, "monthly_management": 0},
    "website": {"setup_min": 700, "setup_max": 2000, "profit_margin": 0.35},
    "raffle": {"setup_and_management": 250},
    "min_budget": 1000,
    "benefit_months": 2
}

def get_api_key():
    return os.environ.get("ANTHROPIC_API_KEY", "")

def get_dynamic_questions(client_intro, answers, api_key):
    system = """You are a business profiling expert for uallak marketing system.
Based on what the client described, generate 2-3 dynamic follow-up questions in Hebrew.
Rules:
- Each question must reference something specific the client said
- Always include an option like: "משהו אחר - ספר לי"
- If business is declining, ask what changed
- If new business with financial pressure, ask about backup plan
- Ask about emotional barriers if relevant
Return JSON only:
{"questions": [{"id": "dynamic_1", "text": "question in Hebrew", "type": "choice", "options": ["option1", "option2", "משהו אחר - ספר לי"]}]}"""

    user_message = f"Client said: {client_intro}\nAnswers so far: {json.dumps(answers)}"
    result = safe_claude_json_call(system, user_message, max_tokens=1000, api_key=api_key)
    return result.get("questions", [])

def build_proposal(answers, api_key, empathy_analysis=None):
    pricing_str = json.dumps(PRICING)

    empathy_block = ""
    if empathy_analysis:
        empathy_block = f"""
SALES INTELLIGENCE (use this to shape tone, not pricing):
- Client profile: {empathy_analysis.get('client_profile', '')}
- Sales approach: {empathy_analysis.get('sales_approach', '')}
- Pricing framing: {empathy_analysis.get('pricing_framing', '')}

Apply this intelligence to:
- business_summary: match the tone and emphasis to this specific client
- honest_note: address their actual fears and drivers, not a generic disclaimer
- self_help_tips: relevant to their exact situation and sophistication level
"""

    system = f"""You are a pricing manager for uallak, an Israeli marketing system for small and medium businesses.
Pricing structure: {pricing_str}
{empathy_block}
CRITICAL RULES:
- If budget is below 1000 NIS: set approved=false
- benefit_value MUST equal monthly_management_total x 2 (two free months)
- Always round DOWN to clean numbers (2580 -> 2500)
- Minimum 35% profit margin on websites
- Keep 15% buffer in targets vs budget
- SEO takes 6+ months for results - always mention this
- self_help_tips must be SPECIFIC to the business type
- All response text must be in Hebrew

Return JSON only with this exact structure:
{{
  "approved": true,
  "rejection_reason": "",
  "business_summary": "Hebrew text",
  "risk_level": "low/medium/high",
  "recommended_services": [],
  "setup_fee_total": 0,
  "monthly_management_total": 0,
  "setup_fee_breakdown": {{}},
  "monthly_breakdown": {{}},
  "goals_90_days": [],
  "kpis": {{}},
  "self_help_tips": [],
  "honest_note": "Hebrew text",
  "benefit_value": 0
}}"""

    user_message = f"Client data: {json.dumps(answers)}"
    return safe_claude_json_call(system, user_message, max_tokens=2000, api_key=api_key)

def run_full_onboarding(client_answers):
    from agents.empathy_agent import analyze_client

    api_key = get_api_key()
    intro = client_answers.get("intro", "")

    print("Empathy read 1 + dynamic questions (parallel)...")
    with ThreadPoolExecutor(max_workers=2) as executor:
        empathy_early_future = executor.submit(analyze_client, {"intro": intro})
        dynamic_questions_future = executor.submit(get_dynamic_questions, intro, client_answers, api_key)
        empathy_early = empathy_early_future.result()
        dynamic_questions = dynamic_questions_future.result()

    print("Empathy read 2 - full conversation...")
    empathy_final = analyze_client(client_answers)

    print("Building proposal...")
    proposal = build_proposal(client_answers, api_key, empathy_analysis=empathy_final)

    print("Running QA check 1 - numbers...")
    from agents.qa_agent import qa_check
    proposal = qa_check(proposal, client_answers)

    print("Running QA check 2 - content...")
    from agents.qa_agent_content import qa_check_content
    proposal = qa_check_content(proposal, client_answers)

    print("Done!")
    return {
        "dynamic_questions": dynamic_questions,
        "empathy_early": empathy_early,
        "empathy_final": empathy_final,
        "proposal": proposal
    }
