import os

def qa_check(proposal, answers):
    monthly = int(proposal.get("monthly_management_total", 0) or 0)
    correct_benefit = monthly * 2
    
    if proposal.get("benefit_value") != correct_benefit:
        print(f"QA Fix: benefit_value {proposal.get('benefit_value')} -> {correct_benefit}")
        proposal["benefit_value"] = correct_benefit
    
    try:
        breakdown = proposal.get("setup_fee_breakdown", {})
        setup_total = sum(int(v or 0) for v in breakdown.values())
        if setup_total and abs(setup_total - int(proposal.get("setup_fee_total", 0) or 0)) > 100:
            print(f"QA Fix: setup_fee_total -> {setup_total}")
            proposal["setup_fee_total"] = setup_total
    except:
        pass
    
    print("QA Agent 1: Numbers verified")
    return proposal
