from anthropic import Anthropic
from datetime import datetime
import json

client = Anthropic()
print("Master Agent ready")

REVIEW_SYSTEM = """You are a quality reviewer for uallak, an Israeli marketing agency.
You receive a marketing proposal and the client's original answers.
Evaluate the proposal against these 7 criteria:

1. Professional and warm tone — not robotic, feels human and trustworthy
2. No unrealistic promises — flag anything like "results within a week", guaranteed ROI, or vague superlatives
3. self_help_tips are genuinely specific to this business type — not generic advice that could apply to any business
4. No contradictions between business_summary and recommended_services — they must tell the same story
5. risk_level is logical given the client's financial status, budget size, and situation described in their answers
6. honest_note reflects genuine honesty — not just sales talk, empty reassurance, or reworded marketing copy
7. Numbers are internally consistent:
   - benefit_value must equal monthly_management_total x 2
   - setup_fee_total must match the sum of all values in setup_fee_breakdown
   - monthly_management_total must match the sum of all values in monthly_breakdown

Return JSON only — no explanation outside the JSON:
{
  "approved": true or false,
  "issues": ["specific issue description", ...],
  "fixed_content": null
}

Set approved=false if ANY criterion fails. Be strict but fair. issues must be empty when approved=true."""


def review_output(label: str, proposal: dict, answers: dict = None) -> dict:
    prompt = f"Proposal:\n{json.dumps(proposal, ensure_ascii=False, indent=2)}"
    if answers:
        prompt += f"\n\nClient answers:\n{json.dumps(answers, ensure_ascii=False, indent=2)}"

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=REVIEW_SYSTEM,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = response.content[0].text.replace("```json", "").replace("```", "").strip()
    result = json.loads(raw)

    print(f"[{datetime.now().isoformat()}] REVIEW [{label}] approved={result.get('approved')} issues={result.get('issues', [])}")

    if not result.get("approved"):
        alert(label, result.get("issues", []))

    return result


def alert(label: str, issues: list):
    print(f"[{datetime.now().isoformat()}] ALERT [{label}] NOT APPROVED")
    for issue in issues:
        print(f"  - {issue}")
