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
    "base_onboarding_fee": 300,
    "min_budget": 1000,
    "benefit_months": 2
}

def get_api_key():
    return os.environ.get("ANTHROPIC_API_KEY", "")

def get_dynamic_questions(client_intro, answers, api_key):
    system = """You are a business profiling expert for uallak marketing system, conducting a warm
~5 minute conversation — not a quick form. Based on what the client described, generate 4-6 dynamic
follow-up questions in Hebrew that make the client feel deeply understood, while also surfacing
material the sales team can use later to handle objections.

Every question must reference something specific the client actually said — never generic filler.
Across the set of questions, make sure to dig into (pick whichever are relevant to THIS client,
don't force all of them if irrelevant):
- What specifically hasn't worked before (if they mentioned past attempts) — get concrete detail,
  not just "it didn't work"
- What a genuinely good outcome would look like to them, in their own terms
- What's stopping them from doing this themselves / in-house
- Any specific worries or hesitations about working with an outside agency

Other rules:
- Always include an option like: "משהו אחר - ספר לי"
- If business is declining, ask what changed
- If new business with financial pressure, ask about backup plan
- Ask about emotional barriers if relevant

Return JSON only:
{"questions": [{"id": "dynamic_1", "text": "question in Hebrew", "type": "choice", "options": ["option1", "option2", "משהו אחר - ספר לי"]}]}"""

    user_message = f"Client said: {client_intro}\nAnswers so far: {json.dumps(answers)}"
    result = safe_claude_json_call(system, user_message, max_tokens=1600, api_key=api_key)
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
- If budget is below 1000 NIS: set approved=false and return an empty "packages" list
- Otherwise, build 1-3 clearly distinct packages (tiers) — e.g. a lighter/essentials option and a
  fuller option — that would each genuinely serve this client. Present them as neutral choices:
  do NOT push the client toward one specific package. Each must be a legitimate fit for a real
  priority/budget level this client could reasonably have, not a "decoy" option
- Every package's benefit_value MUST equal that package's monthly_management_total x 2 (two free months)
- Always round DOWN to clean numbers (2580 -> 2500)
- Minimum 35% profit margin on websites
- Keep 15% buffer in targets vs budget
- SEO takes 6+ months for results - always mention this if any package recommends SEO
- self_help_tips must be SPECIFIC to the business type (shared advice, applies across packages)
- All response text must be in Hebrew
- Every package's setup_fee_total must NEVER be 0, even for the simplest package with only setup=0
  services. Every package includes the base_onboarding_fee ({PRICING['base_onboarding_fee']} NIS) as
  a baseline onboarding/setup charge — include it in that package's setup_fee_breakdown (e.g. key
  "onboarding_setup") on top of any per-service setup fees, and add it into setup_fee_total
- If a package's recommended_services includes "google" or "meta" (paid advertising), honest_note
  MUST clearly explain that the client's actual ad spend/budget (the money spent on ads/credits with
  Google/Meta) is an ADDITIONAL monthly cost on top of monthly_management_total, separate from our
  management fee, and that it will be fully trackable and transparent in their dashboard
- scarcity_note must tell the client, honestly and warmly (not pushy), that they are one of 20
  businesses selected this month for the current 2-free-months management fee benefit

Return JSON only with this exact structure:
{{
  "approved": true,
  "rejection_reason": "",
  "business_summary": "Hebrew text",
  "risk_level": "low/medium/high",
  "goals_90_days": [],
  "kpis": {{}},
  "self_help_tips": [],
  "honest_note": "Hebrew text",
  "scarcity_note": "Hebrew text",
  "packages": [
    {{
      "id": "short-id e.g. light",
      "name": "Hebrew package name",
      "description": "1-2 sentence Hebrew description of who this fits",
      "recommended_services": [],
      "setup_fee_total": 0,
      "monthly_management_total": 0,
      "setup_fee_breakdown": {{}},
      "monthly_breakdown": {{}},
      "benefit_value": 0
    }}
  ]
}}"""

    user_message = f"Client data: {json.dumps(answers)}"
    return safe_claude_json_call(system, user_message, max_tokens=3000, api_key=api_key)

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

    print("Running QA check 2 - content + master review...")
    from agents.qa_agent_content import review_and_fix_proposal
    review_result = review_and_fix_proposal(proposal, client_answers)
    proposal = review_result["proposal"]

    print("Done!")
    return {
        "dynamic_questions": dynamic_questions,
        "empathy_early": empathy_early,
        "empathy_final": empathy_final,
        "proposal": proposal,
        "review": {
            "approved": review_result["review_approved"],
            "issues": review_result["issues"],
        },
    }

def handle_objection(text, packages, answers, empathy_final, api_key):
    """The client typed free text instead of picking one of the presented packages —
    could be an objection, a question, or hesitation. Respond like a skilled, warm
    closer who already knows this client, using their answers and empathy profile to
    address the SPECIFIC thing they said, then steer back toward picking a package
    rather than dead-ending the conversation."""
    system = """You are a skilled, warm, creative closer for uallak, an Israeli marketing agency.
A client was just shown 1-3 package options and, instead of picking one, wrote free text — an
objection, a question, hesitation, or something else entirely. Your job is to keep the conversation
moving toward a decision, never to dead-end it.

Use everything known about this specific client (their answers, the empathy/sales profile) to
respond to what they ACTUALLY said — never a generic reassurance that could apply to anyone.
- If it's an objection or fear: acknowledge it genuinely, then reframe it using their own
  situation/words. Don't be pushy or dismissive.
- If it's a question: answer it clearly and honestly, grounded in the real packages offered
  (their actual services/prices) — never invent numbers not in the packages given.
- If it's unrelated small talk: respond warmly and briefly, then gently guide back to the decision.

End by inviting them back to the packages — reference them briefly by name so it's easy to pick
one, or ask one short clarifying question if that's what's genuinely needed to move forward.
Keep it to 2-5 sentences, warm and human, not corporate. All text in Hebrew.

Return JSON only:
{"reply": "Hebrew text"}"""

    user_message = f"""Packages offered to this client:
{json.dumps(packages, ensure_ascii=False, indent=2)}

Client's answers so far:
{json.dumps(answers, ensure_ascii=False, indent=2)}

Empathy/sales profile for this client:
{json.dumps(empathy_final or {}, ensure_ascii=False, indent=2)}

Instead of picking a package, the client just wrote:
"{text}"
"""
    result = safe_claude_json_call(system, user_message, max_tokens=700, api_key=api_key)
    return result.get("reply", "")
