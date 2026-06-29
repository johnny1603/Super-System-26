import anthropic
import json
import os

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
    client = anthropic.Anthropic(api_key=api_key)
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

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=system,
        messages=[{"role": "user", "content": f"Client said: {client_intro}\nAnswers so far: {json.dumps(answers)}"}]
    )
    raw = response.content[0].text.replace("```json", "").replace("```", "").strip()
    return json.loads(raw).get("questions", [])

def build_proposal(answers, api_key):
    client = anthropic.Anthropic(api_key=api_key)
    pricing_str = json.dumps(PRICING)
    system = f"""You are a pricing manager for uallak, an Israeli marketing system for small and medium businesses.
Pricing structure: {pricing_str}

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

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": f"Client data: {json.dumps(answers)}"}]
    )
    raw = response.content[0].text.replace("```json", "").replace("```", "").strip()
    return json.loads(raw)

def run_full_onboarding(client_answers):
    api_key = get_api_key()
    
    print("Generating dynamic questions...")
    intro = client_answers.get("intro", "")
    dynamic_questions = get_dynamic_questions(intro, client_answers, api_key)
    
    print("Building proposal...")
    proposal = build_proposal(client_answers, api_key)
    
    print("Running QA check 1 - numbers...")
    from agents.qa_agent import qa_check
    proposal = qa_check(proposal, client_answers)
    
    print("Running QA check 2 - content...")
    from agents.qa_agent_content import qa_check_content
    proposal = qa_check_content(proposal, client_answers)
    
    print("Done!")
    return {
        "dynamic_questions": dynamic_questions,
        "proposal": proposal
    }
