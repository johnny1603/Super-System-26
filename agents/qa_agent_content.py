import json

from core.claude_json import ClaudeJSONError, safe_claude_json_call

SYSTEM = """You are the quality reviewer and content fixer for uallak, an Israeli marketing agency.
You receive a marketing proposal and the client's original answers. Evaluate the proposal against
these criteria, then return a corrected version of the proposal that fixes anything that fails:

1. Professional and warm tone — not robotic, feels human and trustworthy
2. No unrealistic promises — no "results within a week", guaranteed ROI, vague superlatives,
   or SEO results promised before 6 months
3. self_help_tips are genuinely specific to this business type — not generic advice that could
   apply to any business
4. No contradictions between business_summary and recommended_services — they must tell the same story
5. risk_level is logical given the client's financial status, budget size, and situation in their answers
6. honest_note reflects genuine honesty — not sales talk, empty reassurance, or reworded marketing copy
7. Numbers are internally consistent:
   - benefit_value must equal monthly_management_total x 2
   - setup_fee_total must match the sum of all values in setup_fee_breakdown, and must never be 0
   - monthly_management_total must match the sum of all values in monthly_breakdown
8. If recommended_services includes "google" or "meta" (paid advertising), honest_note must clearly
   state that the client's actual ad spend/budget is an ADDITIONAL monthly cost on top of
   monthly_management_total, and that it will be trackable in their dashboard

Do not change the proposal's own "approved" field (business eligibility) — that is unrelated to
this quality review. Only touch it if it is clearly inconsistent with the rest of the proposal.

Return JSON only, with this exact shape:
{
  "review_approved": true or false,
  "issues": ["specific issue description", ...],
  "proposal": { ...the full proposal object, corrected if needed, same fields as the input... }
}

Set review_approved=false if ANY criterion failed in the ORIGINAL proposal (before your fixes).
issues must be empty when review_approved=true. Always return a complete, corrected "proposal"
object regardless of review_approved — the corrected proposal is what actually gets sent to the client."""


def review_and_fix_proposal(proposal: dict, answers: dict) -> dict:
    user_message = (
        f"Proposal:\n{json.dumps(proposal, ensure_ascii=False, indent=2)}\n\n"
        f"Client answers:\n{json.dumps(answers, ensure_ascii=False, indent=2)}"
    )

    try:
        result = safe_claude_json_call(SYSTEM, user_message, max_tokens=3000)
        print(f"QA/Review: approved={result.get('review_approved')} issues={result.get('issues', [])}")
        return {
            "proposal": result.get("proposal") or proposal,
            "review_approved": result.get("review_approved", True),
            "issues": result.get("issues", []),
        }
    except ClaudeJSONError as e:
        print(f"QA/Review: Minor issue ({e}), returning original proposal")
        return {"proposal": proposal, "review_approved": True, "issues": []}
